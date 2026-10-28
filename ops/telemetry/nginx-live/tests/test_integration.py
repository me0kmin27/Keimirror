"""Isolated tests against an extracted, never installed Debian nginx."""
import json
import os
from pathlib import Path
import socket
import ssl
import signal
import struct
import concurrent.futures
import subprocess
import tempfile
import time
import unittest

E = Path(os.environ.get('HTTP_MODULE_EVIDENCE', '/home/me0kmin27/.hermes/cache/scratch/keimirror-fulltraffic-evidence/http-module'))
BIN = E / 'binary/usr/sbin/nginx'
MODULE = Path(os.environ.get('HTTP_MODULE_SO', str(E / 'ngx_http_project_live_module.so')))

class Integration(unittest.TestCase):
    def test_00_artifact_loads_and_private_status(self):
        self.assertTrue(MODULE.is_file(), 'built dynamic module is missing')
        with Server() as s:
            data = s.status()
            self.assertEqual(data['schema_version'], 1)
            self.assertEqual(len(data['repositories']), 14)
            self.assertTrue(data['workers'])
            self.assertEqual(set(data), {'schema_version', 'epoch', 'workers', 'repositories'})
            self.assertTrue(all(type(pid) is int for pid in data['workers']))
            self.assertTrue(all(set(row) == {'rx_bytes','tx_bytes','uncertain','active_uncertain'} for row in data['repositories'].values()))

    def test_10_live_progress_before_request_completion(self):
        with Server('limit_rate 32k;') as s:
            (s.path/'archlinux').mkdir()
            (s.path/'archlinux/large').write_bytes(b'x' * (1024*1024))
            before = s.status()['repositories']['archlinux']
            with socket.create_connection(('127.0.0.1', s.port)) as sock:
                request = b'GET /archlinux/large HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'
                sock.sendall(request)
                sock.settimeout(3)
                self.assertIn(b'200 OK', sock.recv(65536))
                first = s.status()['repositories']['archlinux']
                time.sleep(1.2)
                second = s.status()['repositories']['archlinux']
                self.assertGreater(first['tx_bytes'], before['tx_bytes'])
                self.assertGreater(second['tx_bytes'], first['tx_bytes'])
                self.assertEqual(second['rx_bytes'] - before['rx_bytes'], len(request))
                self.assertEqual((s.path/'access').read_bytes(), b'')
                time.sleep(9)
                third = s.status()['repositories']['archlinux']
                self.assertGreater(third['tx_bytes'], second['tx_bytes'])
                self.assertLess(third['tx_bytes'], 1024*1024)
                self.assertEqual((s.path/'access').read_bytes(), b'')
                self.assertEqual(third['uncertain'], 0)
                (E/'live-progress.json').write_text(json.dumps({'repository':'archlinux','initial':before,'open_first':first,'open_second':second,'open_after_10_seconds':third,'completion_log_bytes':0,'response_content_bytes':1024*1024,'assertions':'positive adjacent transmitted deltas while response open; RX equals exact request-header length'},indent=2)+'\n')

class Server:
    def __init__(self, extra='', enabled=True, tls=False):
        self.extra, self.enabled, self.tls = extra, enabled, tls
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='live-', dir=E)
        self.path = Path(self.tmp.name)
        self.sock = str(self.path / 'status.sock')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        tls_conf = ''
        if self.tls:
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=localhost', '-days', '1', '-keyout', str(self.path/'key'), '-out', str(self.path/'cert')], check=True, capture_output=True)
            tls_conf = f'ssl_certificate {self.path}/cert; ssl_certificate_key {self.path}/key;'
        (self.path / 'conf').write_text(f'''load_module {MODULE};
worker_processes 2;
daemon off;
pid {self.path}/pid;
error_log /dev/null crit;
thread_pool live threads=2;
events {{ worker_connections 256; }}
http {{
  access_log off;
  log_format combined_bytes '$bytes_sent';
  project_live_zone;
  client_body_temp_path {self.path}/body;
  proxy_temp_path {self.path}/proxy;
  fastcgi_temp_path {self.path}/fastcgi;
  uwsgi_temp_path {self.path}/uwsgi;
  scgi_temp_path {self.path}/scgi;
  server {{ listen 127.0.0.1:{self.port} {'ssl' if self.tls else ''};
    {tls_conf}
    access_log {self.path}/access combined_bytes;
    project_live {'on' if self.enabled else 'off'};
    root {self.path};
    {self.extra}
  }}
  server {{ listen unix:{self.sock}; project_live off;
    location / {{ project_live_status; }}
  }}
}}
''')
        result = subprocess.run([str(BIN), '-p', str(self.path), '-c', str(self.path/'conf'), '-t'], capture_output=True)
        if result.returncode:
            self.tmp.cleanup()
            raise AssertionError(result.stderr.decode())
        self.proc = subprocess.Popen([str(BIN), '-p', str(self.path), '-c', str(self.path/'conf')], stderr=subprocess.PIPE)
        for _ in range(100):
            if self.proc.poll() is not None:
                raise AssertionError(self.proc.stderr.read().decode())
            try:
                d = self.status()
                if len(d['workers']) == 2:
                    return self
            except (OSError, ValueError):
                pass
            time.sleep(.03)
        self.__exit__(None, None, None)
        raise AssertionError('worker startup timed out')
    def status(self):
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(2)
            sock.connect(self.sock)
            sock.sendall(b'GET / HTTP/1.0\r\n\r\n')
            data = b''
            while part := sock.recv(65536):
                data += part
        return json.loads(data.split(b'\r\n\r\n',1)[1])
    def __exit__(self, *args):
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self.proc.stderr.close()
        self.tmp.cleanup()

if __name__ == '__main__':
    unittest.main(verbosity=2)
