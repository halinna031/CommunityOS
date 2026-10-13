"""Zero-dependency server (stdlib only) exposing the same API as main.py.
Run:  python -m backend.simple_server           -> http://localhost:8000
Handy when you can't `pip install` on the demo machine."""
from __future__ import annotations
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config
from .service import Service, ValidationError

svc = Service()


def _num(v):
    return None if v is None else float(v)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _admin_ok(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Admin-Token", ""), config.ADMIN_TOKEN)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, (config.FRONTEND_DIR / "index.html").read_bytes(), "text/html")
        if path in ("/admin", "/admin.html"):
            return self._send(200, (config.FRONTEND_DIR / "admin.html").read_bytes(), "text/html")
        if path == "/api/meta":
            return self._send(200, svc.meta())
        if path == "/api/admin/reports":
            return self._send(200, svc.admin_reports()) if self._admin_ok() else self._send(401, {"detail": "invalid admin token"})
        if path == "/api/admin/candidates":
            return self._send(200, svc.admin_candidates()) if self._admin_ok() else self._send(401, {"detail": "invalid admin token"})
        self._send(404, {"detail": "not found"})

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/search":
                return self._send(200, svc.search(body.get("query", ""), lat=_num(body.get("lat")), lng=_num(body.get("lng")),
                                                  k=int(body.get("k", 5)), region_id=body.get("region_id", config.DEFAULT_REGION)))
            if self.path == "/api/report":
                return self._send(200, svc.report(body.get("resource_id", ""), body.get("reason", ""), body.get("note", ""), body.get("client_id", "")))
            if self.path == "/api/admin/resolve":
                if not self._admin_ok():
                    return self._send(401, {"detail": "invalid admin token"})
                return self._send(200, svc.admin_resolve(body.get("resource_id", ""), body.get("action", ""), body.get("updates")))
            if self.path == "/api/admin/candidates/resolve":
                if not self._admin_ok():
                    return self._send(401, {"detail": "invalid admin token"})
                return self._send(200, svc.admin_resolve_candidate(body.get("candidate_id", ""), body.get("action", ""), body.get("updates")))
            self._send(404, {"detail": "not found"})
        except (ValidationError, ValueError, json.JSONDecodeError) as e:
            self._send(400, {"detail": str(e)})

    def log_message(self, fmt, *args):  # quieter logs
        pass


if __name__ == "__main__":
    print("CommunityOS (stdlib server) -> http://localhost:8000   admin -> /admin  (token: %s)" % config.ADMIN_TOKEN)
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
