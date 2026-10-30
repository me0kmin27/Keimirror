"""Nonprivileged contracts; sample bytes below are unit fixtures, not runtime evidence."""
import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock, patch
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'ops/telemetry/http-reexec'
sys.path.insert(0, str(SOURCE))
import isolated as h


def fixture(code) -> dict:
    request = b'GET /unit HTTP/1.1\r\n\r\n'
    body = b'x' * 131072 if code == 200 else f'<h1>{code} error</h1>'.encode()
    response = f'HTTP/1.1 {code} Test\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body
    header_size = len(response) - len(body)
    def sample(rx, tx, stamp, completed=0):
        row = dict(rx_bytes=rx, tx_bytes=tx, uncertain=0, active_uncertain=0)
        return dict(monotonic_ns=stamp, wall_ns=stamp, completion_bytes=completed,
                    status=dict(repositories=dict(archlinux=row)))
    return dict(code=code, request_b64=base64.b64encode(request).decode(),
                response_b64=base64.b64encode(response).decode(),
                before=sample(0, 0, 1), first=sample(len(request), header_size+1, 2),
                second=sample(len(request), header_size+2, 3),
                final=sample(len(request), len(response), 4, 4))


class Acceptance(unittest.TestCase):
    def test_listener_fence_waits_for_old_worker_accept_fds(self):
        t = Mock(); backend = Mock()
        backend.old = [10, 100]; backend.m = {'listeners': ['80', '81']}
        t.children.return_value = [[11, 101]]
        t.sockets.side_effect = [{'80', '82'}, {'82'}]
        def wait(check):
            self.assertFalse(check())
            return check()
        backend.wait.side_effect = wait
        proof = h.wait_old_listeners_closed(t, backend)
        self.assertEqual(proof['accepting_listeners'], [])
        self.assertEqual(proof['old'], backend.old)
        self.assertEqual(proof['workers'], [[11, 101]])

    def test_status_rejects_old_worker_and_retains_exact_raw_bytes(self):
        t = Mock(); t.children.return_value = [[22, 202]]
        t.process.return_value = {'identity': [20, 200]}
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'log'; log.write_bytes(b'403\n')
            for workers in ([11], [22]):
                raw = b'HTTP/1.1 200 OK\r\n\r\n' + json.dumps({'workers': workers}).encode()
                peer = Mock(); peer.recv.side_effect = [raw, b'']
                with patch.object(h.socket, 'socket') as factory:
                    factory.return_value.__enter__.return_value = peer
                    if workers == [11]:
                        with self.assertRaises(AssertionError):
                            h.status_sample(t, 'unit-socket', [20, 200], log)
                    else:
                        sample = h.status_sample(t, 'unit-socket', [20, 200], log)
                        self.assertEqual(base64.b64decode(sample['raw_b64']), raw)
                        self.assertEqual(base64.b64decode(sample['completion_b64']), b'403\n')
                        self.assertEqual(sample['workers'], [[22, 202]])

    def test_rendered_nginx_source_preserves_proven_error_locations(self):
        import ast
        tree = ast.parse((SOURCE / 'isolated.py').read_text())
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == 'write_text'
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == 'conf')
        config = eval(compile(ast.Expression(node.args[0]), 'fixture-config', 'eval'),
                      dict(load='/fixture/load', root='/fixture', runtime='/fixture/run', port=12345))
        for code, path in ((403, 'denied'), (404, 'missing'), (416, 'file')):
            self.assertIn(f'location = /archlinux/{path} {{', config)
            self.assertIn(f'access_log /fixture/access-{code} proof;', config)
        self.assertIn('deny all; limit_rate 64;', config)
        self.assertIn("log_format proof '$status';", config)
        self.assertNotIn('error_page', config)

    def test_receipt_retained_byte_exact_private_and_exclusive(self):
        self.assertTrue(hasattr(h, 'retain_acceptance'), 'receipt retention missing')
        import hashlib
        import shutil
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); stage = root / 'disposable'; stage.mkdir()
            raw = b'{"first": {}, "stable": {}, "probes": []}\n'
            (stage / 'acceptance.json').write_bytes(raw)
            proof = {'unit_fixture': True, 'monotonic_ns': 42}
            with patch.object(h.provenance, 'trusted', side_effect=lambda p: p):
                target = h.retain_acceptance(stage, root / 'forward.json', proof)
                with self.assertRaises(FileExistsError):
                    h.retain_acceptance(stage, root / 'forward.json', proof)
            shutil.rmtree(stage)
            self.assertEqual(target.parent, root)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            self.assertEqual((target / 'acceptance.json').read_bytes(), raw)
            manifest = json.loads((target / 'sha256.json').read_bytes())
            self.assertEqual(set(manifest), {'acceptance.json', 'isolated-proof.json'})
            for name, sha in manifest.items():
                self.assertEqual(hashlib.sha256((target / name).read_bytes()).hexdigest(), sha)
                self.assertEqual((target / name).stat().st_mode & 0o777, 0o400)
            self.assertEqual(json.loads((target / 'isolated-proof.json').read_bytes()), proof)

    def test_source_wires_gate_after_listener_fence_and_retains_before_cleanup(self):
        source = (SOURCE / 'isolated.py').read_text()
        compile(source, 'isolated.py', 'exec')
        self.assertIn('backend.accept = isolated_accept', source)
        self.assertIn('wait_old_listeners_closed(t, backend)', source)
        self.assertIn('assert_candidate(proofs)', source)
        self.assertIn("negative['final']['status']['repositories']['archlinux']['uncertain'] >= 1", source)
        self.assertLess(source.index("result['acceptance_directory'] = str(retain_acceptance(stage, output, evidence))"), source.index("['systemctl', 'stop', unit]"))
        self.assertLess(source.index("result['acceptance_directory'] = str(retain_acceptance(stage, output, evidence))"), source.index('shutil.rmtree(root)'))

    def test_all_four_cases_and_exact_whole_wire_inflight_contract(self):
        self.assertTrue(hasattr(h, 'assert_candidate'), 'isolated candidate gate missing')
        proofs = [fixture(code) for code in (200, 403, 404, 416)]
        h.assert_candidate(proofs)
        for codes in ((200, 404), (200, 403, 404), (200, 404, 416)):
            with self.subTest(codes=codes), self.assertRaises(AssertionError):
                h.assert_candidate([p for p in proofs if p['code'] in codes])
        for key, value in (('uncertain', 1), ('active_uncertain', 1), ('rx_bytes', 0), ('tx_bytes', 1)):
            bad = copy.deepcopy(proofs)
            bad[1]['final']['status']['repositories']['archlinux'][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                h.assert_candidate(bad)
        for mutation in ('completed', 'headers_only', 'no_progress', 'late'):
            bad = copy.deepcopy(proofs); p = bad[2]
            if mutation == 'completed': p['second']['completion_bytes'] = 4
            if mutation == 'headers_only': p['second']['status']['repositories']['archlinux']['tx_bytes'] = 1
            if mutation == 'no_progress': p['second'] = copy.deepcopy(p['first'])
            if mutation == 'late': p['second']['monotonic_ns'] = p['final']['monotonic_ns']
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                h.assert_candidate(bad)


if __name__ == '__main__':
    unittest.main()
