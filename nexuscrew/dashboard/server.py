"""Read-only dashboard HTTP server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class DashboardServer:
    """Serve read-only operational snapshots for NexusCrew."""

    def __init__(self, host: str, port: int, snapshot_provider, detail_provider=None):
        self.host = host
        self.port = port
        self.snapshot_provider = snapshot_provider
        self.detail_provider = detail_provider
        self._server = None
        self._thread = None

    def start(self):
        if self._server is not None:
            return
        provider = self.snapshot_provider
        detail_provider = self.detail_provider

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                snapshot = provider()
                routes = {
                    "/healthz": {"ok": True},
                    "/status": snapshot,
                    "/tasks": {"tasks": snapshot.get("tasks", [])},
                    "/approvals": {"approvals": snapshot.get("approvals", [])},
                    "/doctor": {"doctor": snapshot.get("doctor", "")},
                }
                body = routes.get(self.path)
                if body is None and detail_provider is not None:
                    body = detail_provider(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):  # noqa: A003
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None
