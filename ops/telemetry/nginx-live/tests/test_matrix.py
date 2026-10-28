import concurrent.futures
import email.utils
import json
import os
import signal
import socket
import ssl
import struct
import time
import unittest
from test_integration import Server, E

REPOS = ['archlinux', 'artix', 'artix-iso', 'artix-weekly-iso', 'cachyos', 'endeavouros', 'fyralabs', 'gentoo', 'gentoo-portage', 'kali', 'kali-images', 'manjaro', 'opnsense', 'proxmox']

def connect(s):
    sock = socket.create_connection(('127.0.0.1', s.port), timeout=4)
    if s.tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        sock = ctx.wrap_socket(sock, server_hostname='localhost')
    return sock

def exchange(s, path='/archlinux/file', method='GET', headers=b'', body=b''):
    request = method.encode()+b' '+path.encode()+b' HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n'+headers+b'\r\n'+body
    with connect(s) as sock:
        sock.sendall(request)
        data = b''
        while part := sock.recv(65536):
            data += part
    return request, data

def file(s, repo='archlinux', data=b'hello world'):
    (s.path/repo).mkdir(exist_ok=True)
    (s.path/repo/'file').write_bytes(data)

def settled(s, predicate):
    for _ in range(100):
        d = s.status()
        if predicate(d):
            return d
        time.sleep(.02)
    raise AssertionError('counter predicate timed out')

class Matrix(unittest.TestCase):
    def test_live_sendfile_aio_tls_and_gzip_before_log(self):
        for name,extra,tls in [('sendfile','sendfile on;',False),('aio','sendfile on; aio threads=live;',False),('tls','sendfile on; aio threads=live;',True),('gzip','gzip on; gzip_types text/plain; gzip_min_length 1;',False)]:
            with self.subTest(name=name), Server(extra+' limit_rate 32k;',tls=tls) as s:
                file(s,data=os.urandom(1024*1024))
                with connect(s) as sock:
                    sock.sendall(b'GET /archlinux/file HTTP/1.1\r\nHost: localhost\r\nAccept-Encoding: gzip\r\nConnection: close\r\n\r\n')
                    data=sock.recv(32768)
                    self.assertIn(b'200 OK',data)
                    if name=='gzip': self.assertIn(b'Content-Encoding: gzip',data)
                    # Receiving headers can precede send_chain returning and
                    # publishing its counter to a different status worker.
                    # Synchronize only the initial sample, never the delta.
                    deadline=time.monotonic()+.5
                    a=s.status()['repositories']['archlinux']
                    while a['tx_bytes']==0 and time.monotonic()<deadline:
                        time.sleep(min(.005,max(0,deadline-time.monotonic())))
                        a=s.status()['repositories']['archlinux']
                    self.assertLessEqual(time.monotonic(),deadline,
                                         'initial counter publication exceeded 0.5s')
                    self.assertGreater(a['tx_bytes'],0)
                    self.assertLess(a['tx_bytes'],1024*1024)
                    self.assertEqual((s.path/'access').read_bytes(),b'')
                    time.sleep(1.2)
                    b=s.status()['repositories']['archlinux']
                    self.assertGreater(b['tx_bytes'],a['tx_bytes'])
                    self.assertLess(b['tx_bytes'],1024*1024)
                    self.assertEqual(b['uncertain'],0)
                    self.assertEqual((s.path/'access').read_bytes(),b'')

    def test_exact_bytes_plain_sendfile_aio_gzip_tls(self):
        cases = [('memory','sendfile off;',False), ('sendfile','sendfile on;',False), ('aio','sendfile on; aio threads=live;',False), ('gzip','gzip on; gzip_types text/plain; gzip_min_length 1;',False), ('tls','sendfile on; aio threads=live;',True)]
        for name, extra, tls in cases:
            with self.subTest(name=name), Server(extra, tls=tls) as s:
                file(s, data=b'abc123'*50000)
                for method, headers in [('GET',b''), ('HEAD',b''), ('GET',b'Range: bytes=2-101\r\n'), ('GET',b'Range: bytes=2-10,30-40\r\n'), ('GET',b'Accept-Encoding: gzip\r\n'), ('GET',b'If-Modified-Since: '+email.utils.formatdate(int((s.path/'archlinux/file').stat().st_mtime),usegmt=True).encode()+b'\r\n')]:
                    before = s.status()['repositories']['archlinux']
                    req, data = exchange(s, method=method, headers=headers)
                    after = s.status()['repositories']['archlinux']
                    self.assertEqual(after['tx_bytes']-before['tx_bytes'], len(data), (name, method, headers))
                    self.assertEqual(after['rx_bytes']-before['rx_bytes'], len(req))
                    self.assertEqual(after['uncertain'],0)
                    if b'If-Modified-Since' in headers:
                        self.assertIn(b'304 Not Modified',data)
                    if name=='gzip' and b'Accept-Encoding' in headers:
                        self.assertIn(b'Content-Encoding: gzip',data)
                file(s,data=b'')
                before=s.status()['repositories']['archlinux']
                req,data=exchange(s)
                after=s.status()['repositories']['archlinux']
                self.assertEqual(data.split(b'\r\n\r\n',1)[1],b'')
                self.assertEqual(after['tx_bytes']-before['tx_bytes'],len(data))
                self.assertEqual(after['rx_bytes']-before['rx_bytes'],len(req))

    def test_all_projects_concurrent_and_normalization(self):
        with Server('sendfile on; aio threads=live;') as s:
            for repo in REPOS:
                file(s,repo,data=repo.encode()*4000)
            with concurrent.futures.ThreadPoolExecutor(max_workers=14) as pool:
                results=list(pool.map(lambda repo: exchange(s,'/'+repo+'/file'),REPOS))
            d=s.status()['repositories']
            self.assertEqual(set(d),set(REPOS))
            for repo,(req,data) in zip(REPOS,results):
                self.assertEqual(d[repo]['rx_bytes'],len(req))
                self.assertEqual(d[repo]['tx_bytes'],len(data))
                self.assertEqual(d[repo]['uncertain'],0)
            before=d['archlinux']
            req,data=exchange(s,'/ignored/../%61rchlinux//file?private-not-stored')
            after=s.status()['repositories']['archlinux']
            self.assertEqual(after['tx_bytes']-before['tx_bytes'],len(data))
            self.assertEqual(after['rx_bytes']-before['rx_bytes'],len(req))
            self.assertNotIn('private-not-stored',json.dumps(s.status()))

    def test_keepalive_changes_project_and_fd_reuse(self):
        with Server() as s:
            file(s,'archlinux',b'a'*20); file(s,'artix',b'b'*30)
            totals={r:[0,0] for r in ('archlinux','artix')}
            for _ in range(20):
                with connect(s) as sock:
                    for repo in ('archlinux','artix','archlinux'):
                        req=f'GET /{repo}/file HTTP/1.1\r\nHost: localhost\r\n\r\n'.encode()
                        sock.sendall(req)
                        data=b''
                        while b'\r\n\r\n' not in data:
                            data+=sock.recv(4096)
                        head,body=data.split(b'\r\n\r\n',1)
                        length=int(next(x.split(b':',1)[1] for x in head.split(b'\r\n') if x.lower().startswith(b'content-length:')))
                        while len(body)<length:
                            part=sock.recv(4096); body+=part; data+=part
                        totals[repo][0]+=len(req); totals[repo][1]+=len(data)
            d=s.status()['repositories']
            for repo,(rx,tx) in totals.items():
                self.assertEqual((d[repo]['rx_bytes'],d[repo]['tx_bytes']),(rx,tx))
                self.assertEqual(d[repo]['uncertain'],0)

    def test_unsupported_short_body_discard_and_pipeline(self):
        with Server() as s:
            file(s); file(s,'artix')
            for method,headers,body in [('POST',b'Content-Length: 4\r\n',b'data'),('GET',b'Content-Length: 4\r\n',b'data'),('GET',b'Transfer-Encoding: chunked\r\n',b'4\r\ndata\r\n0\r\n\r\n')]:
                before=s.status()['repositories']['archlinux']
                _,data=exchange(s,method=method,headers=headers,body=body)
                after=settled(s,lambda d:d['repositories']['archlinux']['active_uncertain']==0)['repositories']['archlinux']
                self.assertEqual(after['rx_bytes'],before['rx_bytes'])
                self.assertEqual(after['tx_bytes']-before['tx_bytes'],len(data))
                self.assertGreater(after['uncertain'],before['uncertain'])
            before=s.status()['repositories']
            with connect(s) as sock:
                sock.sendall(b'GET /archlinux/file HTTP/1.1\r\nHost: localhost\r\n\r\nGET /artix/file HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                data=b''
                while part:=sock.recv(65536): data+=part
            after=s.status()['repositories']
            for repo in ('archlinux','artix'):
                self.assertGreater(after[repo]['uncertain'],before[repo]['uncertain'])
                self.assertEqual(after[repo]['rx_bytes'],before[repo]['rx_bytes'])
            self.assertEqual(sum(after[r]['tx_bytes']-before[r]['tx_bytes'] for r in REPOS),len(data))

    def test_long_uncertain_and_partial_abort(self):
        with Server('limit_rate 32k; sendfile on; aio threads=live;') as s:
            file(s,data=b'x'*1024*1024)
            with connect(s) as sock:
                sock.sendall(b'GET /archlinux/file HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1\r\nConnection: close\r\n\r\nx')
                sock.recv(4096)
                first=s.status()['repositories']['archlinux']
                self.assertEqual(first['active_uncertain'],1)
                time.sleep(1.2)
                second=s.status()['repositories']['archlinux']
                self.assertGreater(second['tx_bytes'],first['tx_bytes'])
                self.assertEqual(second['active_uncertain'],1)
                sock.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
            final=settled(s,lambda d:d['repositories']['archlinux']['active_uncertain']==0)['repositories']['archlinux']
            self.assertGreater(final['tx_bytes'],0)
            self.assertLess(final['tx_bytes'],1024*1024)
            self.assertEqual(final['uncertain'],1)

    def test_supported_request_abort_and_early_error(self):
        with Server('limit_rate 32k;') as s:
            file(s,data=b'x'*1024*1024)
            with connect(s) as sock:
                sock.sendall(b'GET /archlinux/file HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                sock.recv(4096)
                before=s.status()['repositories']['archlinux']
                self.assertEqual(before['uncertain'],0)
                sock.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
            after=settled(s,lambda d:d['repositories']['archlinux']['uncertain']>0)['repositories']['archlinux']
            self.assertGreaterEqual(after['tx_bytes'],before['tx_bytes'])
            self.assertLess(after['tx_bytes'],1024*1024)
        with Server() as s:
            with connect(s) as sock:
                sock.sendall(b'GET /archlinux/file HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1\r\nContent-Length: 2\r\nConnection: close\r\n\r\n')
                data=b''
                while part:=sock.recv(4096): data+=part
            self.assertIn(b'400 Bad Request',data)
            d=s.status()['repositories']['archlinux']
            self.assertEqual(d['uncertain'],1)
            self.assertEqual(d['rx_bytes'],0)
            self.assertEqual(d['tx_bytes'],len(data))

    def test_status_exclusion_disabled_vhost_and_tcp_denial(self):
        with Server('location /private { project_live_status; }',enabled=False) as s:
            file(s)
            exchange(s)
            _,data=exchange(s,'/private')
            self.assertIn(b'403 Forbidden',data)
            for _ in range(5): s.status()
            for row in s.status()['repositories'].values():
                self.assertEqual(row,dict(rx_bytes=0,tx_bytes=0,uncertain=0,active_uncertain=0))

    def test_error_and_internal_redirect_are_uncertain(self):
        with Server('location = /archlinux/redirect { rewrite ^ /artix/file last; }') as s:
            file(s); file(s,'artix')
            for path in ('/archlinux/missing','/archlinux/redirect'):
                before=s.status()['repositories']['archlinux']
                _,data=exchange(s,path)
                after=s.status()['repositories']['archlinux']
                self.assertGreater(after['uncertain'],before['uncertain'])
                self.assertEqual(after['tx_bytes']-before['tx_bytes'],len(data))
            self.assertEqual(s.status()['repositories']['artix']['tx_bytes'],0)

    def test_http2_is_explicitly_uncertain_not_false_zero(self):
        with Server('http2 on;') as s:
            file(s)
            with connect(s) as sock:
                # HPACK static indexed method/scheme; literal path and authority.
                path=b'/archlinux/file'
                h=b'\x82\x86\x04'+bytes([len(path)])+path+b'\x01\x09localhost'
                def frame(kind,flags,stream,payload):
                    return len(payload).to_bytes(3,'big')+bytes([kind,flags])+stream.to_bytes(4,'big')+payload
                sock.sendall(b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n'+frame(4,0,0,b'')+frame(1,5,1,h))
                data=b''
                for _ in range(20):
                    data+=sock.recv(65536)
                    offset=0; ended=False
                    while offset+9<=len(data):
                        size=int.from_bytes(data[offset:offset+3],'big')
                        if offset+9+size>len(data): break
                        typ,flags=data[offset+3:offset+5]
                        stream=int.from_bytes(data[offset+5:offset+9],'big')
                        if stream==1 and typ in (0,1) and flags&1: ended=True
                        offset+=9+size
                    if ended: break
                self.assertTrue(ended)
            d=s.status()['repositories']['archlinux']
            self.assertEqual(d['uncertain'],1)
            self.assertEqual(d['rx_bytes'],0)
            self.assertEqual(d['tx_bytes'],0)

    def test_reload_epoch_and_heartbeat(self):
        with Server() as s:
            a=s.status()
            time.sleep(1.1)
            b=s.status()
            self.assertEqual(a['epoch'],b['epoch'])
            self.assertEqual(a['workers'],b['workers'])
            pid=a['workers'][0]
            os.kill(pid,signal.SIGSTOP)
            try:
                time.sleep(2.2)
                self.assertNotIn(pid,s.status()['workers'])
            finally:
                os.kill(pid,signal.SIGCONT)
            settled(s,lambda d:set(d['workers'])==set(a['workers']))
            os.kill(s.proc.pid,signal.SIGHUP)
            c=settled(s,lambda d:d['epoch']!=a['epoch'] and len(d['workers'])==2)
            self.assertFalse(set(a['workers'])&set(c['workers']))
            self.assertEqual(len(c['epoch']),32)

if __name__=='__main__': unittest.main(verbosity=2)
