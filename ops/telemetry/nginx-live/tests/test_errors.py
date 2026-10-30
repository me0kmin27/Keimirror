"""Real nginx error accounting; no fabricated counters or completion deltas."""
import json
import socket
import struct
import time
import unittest

from test_integration import E, Server
from test_matrix import connect, exchange, file, settled


class Errors(unittest.TestCase):
    def test_builtin_error_exact_bytes(self):
        # Missing static file, access denial, unsatisfiable static range. In
        # particular 416 clears module ctx in filter-finalize, not the binding.
        for status, path, headers in (
            (404, '/archlinux/missing', b''),
            (403, '/archlinux/denied', b''),
            (416, '/archlinux/file', b'Range: bytes=999999-\r\n'),
        ):
            with self.subTest(status=status), Server(
                'location = /archlinux/denied { deny all; }'
            ) as server:
                file(server)
                before = server.status()['repositories']
                request, response = exchange(server, path, headers=headers)
                self.assertTrue(response.startswith(f'HTTP/1.1 {status} '.encode()))
                head, body = response.split(b'\r\n\r\n', 1)
                self.assertIn(f'<h1>{status} '.encode(), body)
                length = int(next(line.split(b':', 1)[1] for line in head.split(b'\r\n')
                                  if line.lower().startswith(b'content-length:')))
                self.assertEqual(len(body), length)
                after = settled(server, lambda d: (server.path/'access').stat().st_size > 0)['repositories']
                row = after['archlinux']
                self.assertEqual(row['rx_bytes'], len(request))
                self.assertEqual(row['tx_bytes'], len(response))
                self.assertEqual(row['active_uncertain'], 0)
                for repo in before:
                    if repo != 'archlinux':
                        self.assertEqual(after[repo], before[repo])
                # Deliberately last: the RED demonstrates correct measured
                # bytes being rejected solely by the original blanket guard.
                self.assertEqual(row['uncertain'], 0, row)


    def test_builtin_error_inflight_then_exact_completion(self):
        observations = []
        for status, path, headers in (
            (404, '/archlinux/missing', b''),
            (403, '/archlinux/denied', b''),
            (416, '/archlinux/file', b'Range: bytes=999999-\r\n'),
        ):
            with self.subTest(status=status), Server(
                'limit_rate 64; location = /archlinux/denied { deny all; }'
            ) as server:
                file(server)
                with connect(server) as client:
                    request = (b'GET ' + path.encode() + b' HTTP/1.1\r\nHost: localhost\r\n'
                               b'Connection: close\r\n' + headers + b'\r\n')
                    client.sendall(request)
                    response = client.recv(64)
                    first = settled(server, lambda d: d['repositories']['archlinux']['tx_bytes'] > 0)['repositories']['archlinux']
                    self.assertEqual((server.path/'access').read_bytes(), b'')
                    time.sleep(1.2)
                    second = server.status()['repositories']['archlinux']
                    self.assertGreater(second['tx_bytes'], first['tx_bytes'])
                    self.assertEqual((server.path/'access').read_bytes(), b'')
                    self.assertEqual(second['rx_bytes'], len(request))
                    self.assertEqual((second['uncertain'], second['active_uncertain']), (0, 0))
                    while part := client.recv(4096):
                        response += part
                final = settled(server, lambda d: (server.path/'access').stat().st_size > 0)['repositories']['archlinux']
                self.assertTrue(response.startswith(f'HTTP/1.1 {status} '.encode()))
                self.assertIn(f'<h1>{status} '.encode(), response.split(b'\r\n\r\n', 1)[1])
                self.assertEqual(final, dict(rx_bytes=len(request), tx_bytes=len(response), uncertain=0, active_uncertain=0))
                for repo, row in server.status()['repositories'].items():
                    if repo != 'archlinux':
                        self.assertEqual(row, dict(rx_bytes=0, tx_bytes=0, uncertain=0, active_uncertain=0))
                observations.append(dict(status=status, first=first, second=second, final=final,
                                         request_bytes=len(request), response_bytes=len(response),
                                         completion_log_bytes_at_second=0))
        (E/'error-live-progress.json').write_text(json.dumps(observations, indent=2) + '\n')

    def test_error_page_redirects_remain_uncertain(self):
        for target in ('/archlinux/file', '/artix/file', '@fallback'):
            for replacement in ('', '=200 '):
                with self.subTest(target=target, replacement=replacement), Server(
                    f'error_page 404 {replacement}{target}; '
                    'location @fallback { return 404; }'
                ) as server:
                    file(server); file(server, 'artix')
                    request, response = exchange(server, '/archlinux/missing')
                    final = settled(server, lambda d: (server.path/'access').stat().st_size > 0)['repositories']
                    row = final['archlinux']
                    self.assertEqual(row, dict(rx_bytes=len(request), tx_bytes=len(response), uncertain=1, active_uncertain=0))
                    self.assertEqual(final['artix'], dict(rx_bytes=0, tx_bytes=0, uncertain=0, active_uncertain=0))

    def test_416_error_page_reclassification_remains_original_and_unknown(self):
        with Server('error_page 416 =200 /artix/file; '
                    'location = /artix/file { max_ranges 0; }') as server:
            file(server); file(server, 'artix', b'reclassified')
            request, response = exchange(server, headers=b'Range: bytes=999999-\r\n')
            self.assertIn(b'200 OK', response)
            self.assertTrue(response.endswith(b'reclassified'))
            final = settled(server, lambda d: (server.path/'access').stat().st_size > 0)['repositories']
            self.assertEqual(final['archlinux'], dict(rx_bytes=len(request), tx_bytes=len(response), uncertain=1, active_uncertain=0))
            self.assertEqual(final['artix'], dict(rx_bytes=0, tx_bytes=0, uncertain=0, active_uncertain=0))

    def test_body_discard_413_and_unproved_statuses_remain_unknown(self):
        cases = (
            ('/archlinux/missing', 'GET', b'Content-Length: 4\r\n', b'data', 404),
            ('/archlinux/missing', 'GET', b'Transfer-Encoding: chunked\r\n', b'4\r\ndata\r\n0\r\n\r\n', 404),
            ('/archlinux/missing', 'POST', b'Content-Length: 4\r\n', b'data', 404),
            ('/archlinux/missing', 'GET', b'Content-Length: 32\r\n', b'x'*32, 413),
            ('/archlinux/unproved', 'GET', b'', b'', 500),
            ('/archlinux/custom', 'GET', b'', b'', 404),
        )
        for path, method, headers, body, status in cases:
            with self.subTest(path=path, method=method, headers=headers), Server(
                'client_max_body_size 16; lingering_timeout 1s; '
                'location = /archlinux/unproved { return 500; } '
                'location = /archlinux/custom { return 404 "custom"; }'
            ) as server:
                request, response = exchange(server, path, method, headers, body)
                self.assertTrue(response.startswith(f'HTTP/1.1 {status} '.encode()))
                final = settled(server, lambda d: d['repositories']['archlinux']['active_uncertain'] == 0)['repositories']['archlinux']
                self.assertEqual(final['tx_bytes'], len(response))
                self.assertEqual(final['uncertain'], 1)
                self.assertEqual(final['rx_bytes'], 0 if body else len(request))

    def test_builtin_error_abort_is_still_unknown(self):
        with Server('limit_rate 64;') as server:
            with connect(server) as client:
                client.sendall(b'GET /archlinux/missing HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                self.assertTrue(client.recv(64))
                first = settled(server, lambda d: d['repositories']['archlinux']['tx_bytes'] > 0)['repositories']['archlinux']
                self.assertEqual(first['uncertain'], 0)
                client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            final = settled(server, lambda d: d['repositories']['archlinux']['uncertain'] == 1)['repositories']['archlinux']
            self.assertGreaterEqual(final['tx_bytes'], first['tx_bytes'])
            self.assertEqual(final['active_uncertain'], 0)

    def test_head_tls_and_http10_errors(self):
        for tls in (False, True):
            with self.subTest(tls=tls), Server(tls=tls) as server:
                for method in ('GET', 'HEAD'):
                    before = server.status()['repositories']['archlinux']
                    with connect(server) as client:
                        request = method.encode() + b' /archlinux/missing HTTP/1.0\r\nHost: localhost\r\n\r\n'
                        client.sendall(request)
                        response = b''
                        while part := client.recv(4096):
                            response += part
                    self.assertIn(b'404 Not Found', response)
                    if method == 'HEAD':
                        self.assertEqual(response.split(b'\r\n\r\n', 1)[1], b'')
                    row = server.status()['repositories']['archlinux']
                    self.assertEqual(row['rx_bytes']-before['rx_bytes'], len(request))
                    self.assertEqual(row['tx_bytes']-before['tx_bytes'], len(response))
                    self.assertEqual((row['uncertain'], row['active_uncertain']), (0, 0))

    def test_keepalive_errors_do_not_rebind_or_double_count(self):
        with Server('location = /archlinux/denied { deny all; }') as server:
            file(server); file(server, 'artix')
            totals = {repo: [0, 0] for repo in ('archlinux', 'artix')}
            with connect(server) as client:
                for path, status, headers in (
                    ('/archlinux/missing', 404, b''),
                    ('/artix/file', 200, b''),
                    ('/archlinux/file', 416, b'Range: bytes=999999-\r\n'),
                    ('/archlinux/denied', 403, b''),
                    ('/artix/file', 200, b''),
                ):
                    request = b'GET ' + path.encode() + b' HTTP/1.1\r\nHost: localhost\r\n' + headers + b'\r\n'
                    client.sendall(request)
                    response = b''
                    while b'\r\n\r\n' not in response:
                        part = client.recv(4096)
                        self.assertTrue(part)
                        response += part
                    head, body = response.split(b'\r\n\r\n', 1)
                    length = int(next(line.split(b':', 1)[1] for line in head.split(b'\r\n')
                                      if line.lower().startswith(b'content-length:')))
                    while len(body) < length:
                        part = client.recv(4096)
                        self.assertTrue(part)
                        body += part
                        response += part
                    self.assertTrue(response.startswith(f'HTTP/1.1 {status} '.encode()))
                    repo = path.split('/')[1]
                    totals[repo][0] += len(request)
                    totals[repo][1] += len(response)
            final = settled(server, lambda d: all(d['repositories'][r]['tx_bytes'] == counts[1]
                                                 for r, counts in totals.items()))['repositories']
            for repo, (rx, tx) in totals.items():
                self.assertEqual(final[repo], dict(rx_bytes=rx, tx_bytes=tx, uncertain=0, active_uncertain=0))

    def test_subrequest_error_output_stays_with_main_project(self):
        with Server('ssi on; ssi_types text/plain;') as server:
            file(server, data=b'before<!--# include virtual="/artix/missing" -->after')
            request, response = exchange(server)
            self.assertIn(b'200 OK', response)
            self.assertIn(b'before', response)
            self.assertIn(b'after', response)
            self.assertNotIn(b'<!--#', response)
            final = settled(server, lambda d: (server.path/'access').stat().st_size > 0)['repositories']
            self.assertEqual(final['archlinux'], dict(rx_bytes=len(request), tx_bytes=len(response), uncertain=0, active_uncertain=0))
            self.assertEqual(final['artix'], dict(rx_bytes=0, tx_bytes=0, uncertain=0, active_uncertain=0))


if __name__ == '__main__':
    unittest.main(verbosity=2)
