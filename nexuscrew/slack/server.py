"""Slack slash command HTTP server."""
import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs


class SlackCommandServer:
    """Receive Slack slash commands and return text responses."""

    def __init__(self, host: str, port: int, signing_secret: str, handler):
        self.host = host
        self.port = port
        self.signing_secret = signing_secret.encode("utf-8") if signing_secret else b""
        self.handler = handler
        self._server = None
        self._thread = None

    def start(self):
        if self._server is not None:
            return
        signing_secret = self.signing_secret
        handler = self.handler

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != "/slack/commands":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                if signing_secret and not _verify_slack_signature(
                    signing_secret,
                    self.headers.get("X-Slack-Signature", ""),
                    self.headers.get("X-Slack-Request-Timestamp", ""),
                    body,
                ):
                    self.send_response(401)
                    self.end_headers()
                    return
                form = {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}
                response = handler(form)
                payload = json.dumps({"response_type": "ephemeral", "text": response}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

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


def _verify_slack_signature(secret: bytes, signature: str, timestamp: str, body: bytes) -> bool:
    if not timestamp:
        return False
    if abs(time.time() - int(timestamp)) > 60 * 5:
        return False
    basestring = f"v0:{timestamp}:".encode("utf-8") + body
    expected = "v0=" + hmac.new(secret, basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)
