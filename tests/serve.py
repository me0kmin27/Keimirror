"""Loopback-only static server for parallel browser tests (not production)."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from functools import partial


class TestHTTPServer(ThreadingHTTPServer):
    # Older Python versions default to five queued connections. Concurrent
    # browsers each open several sockets while fetching nested components.
    request_queue_size = 128


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    with TestHTTPServer(("127.0.0.1", 18763), handler) as server:
        server.serve_forever()
