#!/usr/bin/env python3
"""serve.py — static server for the GeoLibre map views, 127.0.0.1 only, stdlib only.

    python3 serve.py <app_dir> <data_dir> <port> [<override_dir>] [--ready FILE]

  /                 the GeoLibre web-app bundle (geolibre/static/app)
  /<name>           a file in <override_dir> shadows the bundle file of the same name (deployment.json,
                    plugin-registry.json); nothing else is looked up there
  /data/<path>      a file under <data_dir> (the migration folder)

HTTP Range (single range) is honoured everywhere: 206 with Content-Range, 416 when the start is past the end.
Hardening on top of the founder's tested kit: the /data/ and override paths are percent-decoded, resolved with
realpath (symlinks followed) and must stay inside their root, else 403 (`../`, `%2e%2e`, `%2f`, NUL, absolute
paths, symlinks out); no directory listings; the Host header must name this server (127.0.0.1/localhost:<port>,
against DNS rebinding); CORS is granted only to this server's own origin (the embed is same-origin); /data/ responses
carry `Cache-Control: no-store` so a regenerated view is never served stale.
`--ready FILE` is written ({"port", "pid"}) once the socket is bound, so start.py knows the bind succeeded.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import sys
import urllib.parse

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")
EXTRA_TYPES = {".wasm": "application/wasm", ".js": "text/javascript", ".mjs": "text/javascript",
               ".json": "application/json", ".geojson": "application/geo+json", ".tif": "image/tiff",
               ".tiff": "image/tiff", ".pbf": "application/x-protobuf", ".pmtiles": "application/octet-stream"}


def inside(root: str, rel: str) -> str | None:
    """realpath of root/rel when it stays inside root (root itself excluded), else None. `rel` is already decoded."""
    if "\x00" in rel:
        return None
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return None
    if any(part == ".." for part in rel.split("/")):
        return None
    base = os.path.realpath(root)
    cand = os.path.realpath(os.path.join(base, rel))
    if cand == base or not cand.startswith(base + os.sep):
        return None
    return cand


class Handler(http.server.SimpleHTTPRequestHandler):
    data_dir: str = ""
    override_dir: str | None = None
    port: int = 0
    server_version = "axis-map/1"
    sys_version = ""

    # ---------------------------------------------------------------------------------------------- routing
    def _route(self) -> tuple[str | None, int]:
        """(filesystem path, 0) or (None, http status to send)."""
        raw = self.path.split("?", 1)[0].split("#", 1)[0]
        if not raw.startswith("/"):
            return None, 400
        dec = urllib.parse.unquote(raw, errors="strict") if "%" in raw else raw
        if raw.startswith("/data/") or raw == "/data":
            rel = dec[len("/data/"):] if dec.startswith("/data/") else ""
            if not rel:
                return None, 403
            p = inside(self.data_dir, rel)
            if p is None:
                return None, 403
            return p, 0
        if self.override_dir:
            name = dec.lstrip("/")
            if name and "/" not in name:
                p = inside(self.override_dir, name)
                if p and os.path.isfile(p):
                    return p, 0
        # the bundle: the stock translate_path drops '..' and empty segments, then we confirm it stays inside
        p = super().translate_path(raw)
        base = os.path.realpath(self.directory)
        rp = os.path.realpath(p)
        if rp != base and not rp.startswith(base + os.sep):
            return None, 403
        return p, 0

    def translate_path(self, path):  # used by the stock send_head for the bundle
        p, status = self._route()
        return p if p is not None else os.path.join(self.directory, "\x00forbidden")

    def list_directory(self, path):
        self.send_error(404, "no directory listings")
        return None

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        return host in {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    # ---------------------------------------------------------------------------------------------- headers
    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        origin = self.headers.get("Origin") if hasattr(self, "headers") and self.headers else None
        if origin and origin in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Expose-Headers", "Content-Range, Content-Length, Accept-Ranges")
            self.send_header("Vary", "Origin")
        if self.path.startswith("/data/"):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_OPTIONS(self):
        if not self._host_ok():
            self.send_error(403, "host not allowed")
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_head(self):
        self._left = None
        if not self._host_ok():
            self.send_error(403, "host not allowed")
            return None
        try:
            path, status = self._route()
        except UnicodeDecodeError:
            path, status = None, 400
        if path is None:
            self.send_error(status)
            return None
        if self.path.startswith("/data/") and not os.path.isfile(path):
            self.send_error(404)
            return None
        m = RANGE_RE.match(self.headers.get("Range", "").strip())
        if not m or not os.path.isfile(path) or not any(m.groups()):
            return super().send_head()
        size = os.path.getsize(path)
        a, b = m.groups()
        if a:
            start = int(a)
            end = min(int(b), size - 1) if b else size - 1
            if b and int(b) < start:
                return super().send_head()      # malformed range: ignore it (RFC 9110 §14.2)
        else:
            n = int(b)
            if n == 0:
                self.send_error(416)
                return None
            start, end = max(0, size - n), size - 1
        if start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._left = end - start + 1
        return f

    def copyfile(self, src, dst):
        n = getattr(self, "_left", None)
        if n is None:
            return super().copyfile(src, dst)
        while n > 0:
            c = src.read(min(65536, n))
            if not c:
                break
            dst.write(c)
            n -= len(c)

    def log_message(self, fmt, *a):
        sys.stderr.write("%s\n" % (fmt % a))


Handler.extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, **EXTRA_TYPES}


def make_server(app: str, data: str, port: int, override: str | None = None) -> http.server.ThreadingHTTPServer:
    H = type("H", (Handler,), {"data_dir": os.path.realpath(data),
                               "override_dir": os.path.realpath(override) if override else None})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(H, directory=app))
    srv.daemon_threads = True
    H.port = srv.server_address[1]
    return srv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Serve the GeoLibre app and a data folder on 127.0.0.1 (Range aware).")
    ap.add_argument("app")
    ap.add_argument("data")
    ap.add_argument("port", type=int)
    ap.add_argument("override", nargs="?")
    ap.add_argument("--ready", help="write {port, pid} here once bound")
    a = ap.parse_args(argv)
    try:
        srv = make_server(a.app, a.data, a.port, a.override)
    except OSError as e:
        print(f"serve.py: bind 127.0.0.1:{a.port} failed: {e}", file=sys.stderr)
        return 13
    if a.ready:
        tmp = a.ready + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"port": srv.server_address[1], "pid": os.getpid()}, f)
        os.replace(tmp, a.ready)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
