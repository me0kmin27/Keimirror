"""Public status aggregation never relays arbitrary server/log data."""
import importlib.util
import io
import json
import stat
import tempfile
from pathlib import Path
import unittest
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    'archlinux': ['archlinux'], 'gentoo': ['gentoo', 'gentoo-portage'],
    'kali': ['kali', 'kali-images'], 'cachyos': ['cachyos'],
    'artix': ['artix', 'artix-iso', 'artix-weekly-iso'],
    'opnsense': ['opnsense'], 'endeavouros': ['endeavouros'],
    'fyralabs': ['fyralabs'], 'manjaro': ['manjaro'], 'proxmox': ['proxmox'],
}
NOW = 1793170800


def metric():
    return dict(state='unknown', last_attempt_at=None, last_finished_at=None,
                last_success_at=None, exit_code=None, warning=False, size_bytes=None,
                size_measured_at=None, sync_rx_bps=None, sync_tx_bps=None,
                rates_observed_at=None, http_bytes_60s=None, rsync_bytes_60s=None,
                traffic_observed_since=None)


def snapshot(mirror='mirror1') -> dict[str, Any]:
    return dict(schema_version=1, mirror_id=mirror, collected_at=NOW, interval_seconds=15,
                projects={key: dict(**metric(), repositories=[dict(id=r, **metric()) for r in ids])
                          for key, ids in GROUPS.items()})


class AggregationTests(unittest.TestCase):
    def load(self):
        path = ROOT / 'telemetry/aggregate.py'
        self.assertTrue(path.exists(), 'The privacy-preserving aggregator is not implemented')
        spec = importlib.util.spec_from_file_location('keimirror_aggregate', path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_valid_snapshot_is_allowlisted_not_relayed_verbatim(self):
        module = self.load()
        value = snapshot()
        value['private_key'] = 'MUST-NOT-LEAVE-THE-MIRROR'
        value['projects']['archlinux']['raw_log'] = 'client-address path'
        clean = module.sanitize_snapshot(value, 'mirror1', NOW)
        self.assertEqual(clean, snapshot())

    def test_invalid_identity_and_numeric_values_are_rejected(self):
        module = self.load()
        mutations = [
            lambda v: v.update(mirror_id='mirror2'),
            lambda v: v.update(schema_version=True),
            lambda v: v.update(collected_at=float('nan')),
            lambda v: v.update(collected_at=NOW + 31),
            lambda v: v.update(interval_seconds=0),
            lambda v: v['projects'].pop('gentoo'),
            lambda v: v['projects'].update(untrusted={}),
            lambda v: v['projects']['archlinux'].update(size_bytes=-1),
            lambda v: v['projects']['archlinux'].update(size_bytes=True),
            lambda v: v['projects']['archlinux'].update(size_bytes=2**53),
            lambda v: v['projects']['archlinux'].update(sync_rx_bps=float('inf')),
            lambda v: v['projects']['archlinux'].update(state='ok'),
            lambda v: v['projects']['archlinux'].update(warning='false'),
            lambda v: v['projects']['archlinux']['repositories'].append(dict(id='archlinux', **metric())),
            lambda v: v['projects']['archlinux']['repositories'][0].update(id='unknown'),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                value = snapshot()
                mutation(value)
                with self.assertRaises(ValueError):
                    module.sanitize_snapshot(value, 'mirror1', NOW)

    def test_group_cannot_report_complete_while_repository_is_unknown(self):
        module = self.load()
        value = snapshot()
        project = value['projects']['gentoo']
        project.update(state='completed', exit_code=0, last_finished_at=NOW,
                       last_success_at=NOW)
        project['repositories'][0].update(state='completed', exit_code=0,
                                          last_finished_at=NOW, last_success_at=NOW)
        # Only gentoo-portage is unknown; parent completion evidence is valid.
        with self.assertRaisesRegex(ValueError, 'Inconsistent group state'):
            module.sanitize_snapshot(value, 'mirror1', NOW)
        project['state'] = 'unknown'
        self.assertEqual(module.sanitize_snapshot(value, 'mirror1', NOW), value)

    def test_completed_state_requires_success_evidence_not_only_matching_badges(self):
        module = self.load()
        for exit_code, warning, success, valid in [(None, False, None, False),
                                                  (5, False, NOW, False),
                                                  (24, False, NOW, False),
                                                  (0, False, NOW, True),
                                                  (24, True, NOW, True)]:
            value = snapshot()
            project = value['projects']['archlinux']
            for record in (project, project['repositories'][0]):
                record.update(state='completed', exit_code=exit_code, warning=warning,
                              last_finished_at=NOW, last_success_at=success)
            with self.subTest(exit_code=exit_code, warning=warning, success=success):
                if valid:
                    module.sanitize_snapshot(value, 'mirror1', NOW)
                else:
                    with self.assertRaises(ValueError):
                        module.sanitize_snapshot(value, 'mirror1', NOW)

    def test_partial_outage_preserves_old_timestamp_and_other_mirror_recovers(self):
        module = self.load()
        self.assertTrue(callable(getattr(module, 'collect', None)), 'Collection is missing')
        previous = module.collect(lambda mirror: snapshot(mirror), now=NOW)

        def interrupted(mirror):
            if mirror == 'mirror1':
                raise TimeoutError('PRIVATE transport details')
            value = snapshot(mirror)
            value['collected_at'] = NOW + 90
            return value

        result = module.collect(interrupted, previous=previous, now=NOW + 90)
        self.assertEqual(result['generated_at'], NOW + 90)
        self.assertFalse(result['mirrors']['mirror1']['available'])
        self.assertEqual(result['mirrors']['mirror1']['snapshot']['collected_at'], NOW)
        self.assertTrue(result['mirrors']['mirror2']['available'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_stale_or_corrupt_cache_never_becomes_live_data(self):
        module = self.load()
        self.assertTrue(callable(getattr(module, 'collect', None)), 'Collection is missing')
        result = module.collect(lambda mirror: snapshot(mirror), now=NOW + 61)
        self.assertTrue(all(not entry['available'] for entry in result['mirrors'].values()))
        corrupted = {'mirrors': {'mirror1': {'snapshot': {'private': 'secret'}}}}
        result = module.collect(lambda _: (_ for _ in ()).throw(OSError()), previous=corrupted, now=NOW)
        self.assertEqual(result['mirrors']['mirror1'], {'available': False, 'snapshot': None})

    def test_http_fetch_is_fixed_bounded_json_and_does_not_follow_redirects(self):
        module = self.load()
        self.assertTrue(callable(getattr(module, 'fetch_snapshot', None)), 'HTTP fetch is missing')

        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'application/json; charset=utf-8'}

        class Opener:
            def __init__(self, response):
                self.response = response
                self.calls = []

            def open(self, request, timeout):
                self.calls.append((request.full_url, timeout))
                return self.response

        opener = Opener(Response(json.dumps(snapshot()).encode()))
        self.assertEqual(module.fetch_snapshot('mirror1', opener=opener), snapshot())
        self.assertEqual(opener.calls, [('https://mirror.keiminem.com/.well-known/keimirror-status.json', 4)])
        for payload, content_type, status in [(b'x' * (262144 + 1), 'application/json', 200),
                                               (b'{}', 'text/html', 200),
                                               (b'{}', 'application/json', 302)]:
            response = Response(payload)
            response.headers = {'Content-Type': content_type}
            response.status = status
            with self.subTest(content_type=content_type, status=status):
                with self.assertRaises(ValueError):
                    module.fetch_snapshot('mirror1', opener=Opener(response))
        self.assertIsNone(module.NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'http://internal/'))

    def test_cli_publishes_atomic_public_json_and_preserves_cache_on_fetch_failure(self):
        module = self.load()
        self.assertTrue(callable(getattr(module, 'main', None)), 'Runnable aggregator is missing')
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'public/mirrors.json'
            module.main(['--output', str(output)], fetcher=lambda mirror: snapshot(mirror), now=NOW)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o644)
            first = json.loads(output.read_text())
            self.assertTrue(first['mirrors']['mirror1']['available'])
            module.main(['--output', str(output)], fetcher=lambda _: (_ for _ in ()).throw(OSError()), now=NOW + 16)
            second = json.loads(output.read_text())
            self.assertFalse(second['mirrors']['mirror1']['available'])
            self.assertEqual(second['mirrors']['mirror1']['snapshot']['collected_at'], NOW)
            before = output.read_bytes()
            with patch.object(module.os, 'replace', side_effect=OSError('blocked rename')):
                with self.assertRaises(OSError):
                    module.main(['--output', str(output)], fetcher=lambda mirror: snapshot(mirror), now=NOW + 17)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual([p.name for p in output.parent.iterdir()], ['mirrors.json'])


if __name__ == '__main__':
    unittest.main()
