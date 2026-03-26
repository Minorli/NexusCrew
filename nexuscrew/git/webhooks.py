"""GitHub webhook receiver."""
import hmac
import json
import threading
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class GitHubWebhookServer:
    """Receive GitHub webhook events and forward them into NexusCrew."""

    def __init__(self, host: str, port: int, secret: str, handler):
        self.host = host
        self.port = port
        self.secret = secret.encode("utf-8") if secret else b""
        self.handler = handler
        self._server = None
        self._thread = None

    def start(self):
        if self._server is not None:
            return
        secret = self.secret
        callback = self.handler

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/github/webhook":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                if secret:
                    signature = self.headers.get("X-Hub-Signature-256", "")
                    expected = "sha256=" + hmac.new(secret, body, sha256).hexdigest()
                    if not hmac.compare_digest(signature, expected):
                        self.send_response(401)
                        self.end_headers()
                        return
                event = self.headers.get("X-GitHub-Event", "")
                delivery_id = self.headers.get("X-GitHub-Delivery", "")
                payload = json.loads(body.decode("utf-8"))
                callback(event, payload, delivery_id)
                self.send_response(202)
                self.end_headers()

            def log_message(self, format, *args):  # noqa: A003
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None
