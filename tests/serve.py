"""Loopback-only static server for parallel browser tests (not production)."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from functools import partial
import json
import time
from urllib.parse import urlsplit


class TestHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path == '/status/mirrors.json':
            # Default test-only fixture: no collectors are running locally.
            # Success readings belong only in explicitly intercepted browser tests.
            data = {
                'schema_version': 1, 'generated_at': int(time.time()), 'refresh_seconds': 15,
                'mirrors': {key: {'available': False, 'snapshot': None}
                            for key in ('mirror1', 'mirror2')},
            }
            body = json.dumps(data).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


class TestHTTPServer(ThreadingHTTPServer):
    # Older Python versions default to five queued connections. Concurrent
    # browsers each open several sockets while fetching nested components.
    request_queue_size = 128


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    handler = partial(TestHandler, directory=str(root))
    with TestHTTPServer(("127.0.0.1", 18763), handler) as server:
        server.serve_forever()
