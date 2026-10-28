"""Real loopback upgrades: never label unobserved tunnel RX healthy."""
import json
import socket
import threading
import time
import unittest

from test_integration import E, Server
from test_matrix import settled


class Upgrade(unittest.TestCase):
    def test_101_without_upgrade_request_is_uncertain(self):
        # Response-side defense must not depend on an incoming Upgrade header.
        with Server('location /archlinux/switch { return 101; }') as server:
            with socket.create_connection(('127.0.0.1', server.port), timeout=5) as client:
                client.sendall(b'GET /archlinux/switch HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                response = b''
                while part := client.recv(4096):
                    response += part
            self.assertTrue(response.startswith(b'HTTP/1.1 101 '), response)
            final = settled(server, lambda d: (server.path / 'access').stat().st_size > 0)['repositories']['archlinux']
            self.assertEqual(final['uncertain'], 1, final)
            self.assertEqual(final['active_uncertain'], 0)

    def test_tunnel_rx_after_handshake_stays_uncertain_until_cleanup(self):
        accepted = threading.Event()
        release = threading.Event()
        errors = []
        received = []
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(5)

            def upstream():
                try:
                    with listener.accept()[0] as peer:
                        peer.settimeout(5)
                        header = b''
                        while b'\r\n\r\n' not in header:
                            part = peer.recv(4096)
                            if not part:
                                raise AssertionError('upstream request closed early')
                            header += part
                        self.assertEqual(header.split(b'\r\n\r\n', 1)[1], b'')
                        accepted.set()
                        if not release.wait(5):
                            raise AssertionError('handshake release timed out')
                        peer.sendall(b'HTTP/1.1 101 Switching Protocols\r\n'
                                     b'Connection: upgrade\r\nUpgrade: test-tunnel\r\n\r\n')
                        while part := peer.recv(4096):
                            received.append(part)
                            peer.sendall(part)
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=upstream)
            thread.start()
            observations = []
            try:
                port = listener.getsockname()[1]
                extra = f'''location /archlinux/tunnel {{
                    proxy_pass http://127.0.0.1:{port};
                    proxy_http_version 1.1;
                    proxy_set_header Upgrade $http_upgrade;
                    proxy_set_header Connection upgrade;
                    proxy_read_timeout 10s;
                }}'''
                with Server(extra) as server:
                    with socket.create_connection(('127.0.0.1', server.port), timeout=5) as client:
                        client.sendall(b'GET /archlinux/tunnel HTTP/1.1\r\nHost: localhost\r\n'
                                       b'Connection: upgrade\r\nUpgrade: test-tunnel\r\n\r\n')
                        self.assertTrue(accepted.wait(5), 'upstream did not receive request')
                        observations.append(server.status()['repositories']['archlinux'])
                        release.set()
                        response = b''
                        while b'\r\n\r\n' not in response:
                            part = client.recv(4096)
                            self.assertTrue(part, 'handshake closed early')
                            response += part
                        self.assertIn(b'101 Switching Protocols', response)
                        # No client tunnel data is sent until the complete 101 arrives.
                        for index in range(3):
                            payload = bytes([65 + index]) * 2048
                            client.sendall(payload)
                            echo = b''
                            while len(echo) < len(payload):
                                part = client.recv(4096)
                                self.assertTrue(part, 'tunnel closed early')
                                echo += part
                            self.assertEqual(echo, payload)
                            time.sleep(.15)
                            observations.append(server.status()['repositories']['archlinux'])
                            self.assertEqual((server.path / 'access').read_bytes(), b'')
                    final = settled(server, lambda d: (server.path / 'access').stat().st_size > 0)['repositories']['archlinux']
                thread.join(5)
                self.assertFalse(thread.is_alive(), 'upstream did not stop')
                self.assertEqual(errors, [])
                self.assertEqual(b''.join(received), b'A' * 2048 + b'B' * 2048 + b'C' * 2048)
                (E / 'upgrade-observations.json').write_text(json.dumps({
                    'open': observations, 'closed': final,
                    'verified_client_tunnel_bytes_after_101': sum(map(len, received)),
                }, indent=2) + '\n')
                for index, row in enumerate(observations):
                    with self.subTest(observation=index):
                        self.assertEqual(row['active_uncertain'], 1, row)
                        self.assertEqual(row['uncertain'], 1, row)
                self.assertEqual(final['active_uncertain'], 0)
                self.assertEqual(final['uncertain'], 1)
            finally:
                release.set()
                thread.join(6)


if __name__ == '__main__':
    unittest.main(verbosity=2)
