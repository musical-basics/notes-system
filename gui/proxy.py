#!/usr/bin/env python3
"""Local dashboard server + Supabase proxy.

Serves gui/ static files AND proxies /rest/v1/* to Supabase, injecting the secret
key SERVER-SIDE. The browser never sees the key — it calls same-origin /rest/v1/...
This is required because Supabase blocks secret (sb_secret_) keys used from a browser.

Reads SUPABASE_URL + SUPABASE_SERVICE_KEY from the repo .env. Stdlib only.
Run via ./gui/serve.sh  (or: python3 gui/proxy.py [port])
"""
import os
import sys
import http.server
import socketserver
import urllib.request
import urllib.error

GUI_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(GUI_DIR)


def load_dotenv():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()
try:
    SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
    SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
except KeyError as e:
    sys.exit(f"Missing {e} — fill in {ROOT}/.env (copy from .env.example).")

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
PASSTHROUGH_HEADERS = ("Content-Type", "Prefer", "Range", "Accept")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=GUI_DIR, **k)

    def _proxy(self):
        target = f"{SUPABASE_URL}{self.path}"   # self.path is /rest/v1/...?query
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(target, data=body, method=self.command)
        req.add_header("apikey", SUPABASE_KEY)
        req.add_header("Authorization", f"Bearer {SUPABASE_KEY}")
        for h in PASSTHROUGH_HEADERS:
            if self.headers.get(h):
                req.add_header(h, self.headers[h])
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data, status = r.read(), r.status
                ctype = r.headers.get("Content-Type", "application/json")
                crange = r.headers.get("Content-Range")
        except urllib.error.HTTPError as e:
            data, status = e.read(), e.code
            ctype = e.headers.get("Content-Type", "application/json")
            crange = None
        except urllib.error.URLError as e:
            data = f'{{"message":"proxy: cannot reach Supabase: {e.reason}"}}'.encode()
            status, ctype, crange = 502, "application/json", None
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if crange:
            self.send_header("Content-Range", crange)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/rest/v1/"):
            return self._proxy()
        return super().do_GET()

    def do_PATCH(self):
        if self.path.startswith("/rest/v1/"):
            return self._proxy()
        self.send_error(405)

    def do_POST(self):
        if self.path.startswith("/rest/v1/"):
            return self._proxy()
        self.send_error(405)

    def log_message(self, fmt, *args):
        sys.stderr.write("  " + (fmt % args) + "\n")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    print(f"Notes dashboard + Supabase proxy -> http://localhost:{PORT}")
    print(f"  proxying /rest/v1/* to {SUPABASE_URL} (key stays server-side)")
    print("  Ctrl-C to stop.")
    with Server(("127.0.0.1", PORT), Handler) as httpd:
        httpd.serve_forever()
