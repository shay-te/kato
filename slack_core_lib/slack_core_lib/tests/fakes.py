"""A local HTTP server answering like Slack's ``chat.postMessage``."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeSlack(object):
    """Records every request; answers ``answer`` (``(status, body)``)."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.answer: tuple[int, object] = (200, {'ok': True, 'ts': '1.2'})
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - the http.server name
                length = int(self.headers.get('Content-Length') or 0)
                fake.requests.append({
                    'path': self.path,
                    'authorization': self.headers.get('Authorization', ''),
                    'body': json.loads(self.rfile.read(length) or b'{}'),
                })
                status, body = fake.answer
                raw = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):  # quiet
                pass

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self._server.server_address[1]}/api'
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
