#!/usr/bin/env python3
"""render_check.py — open view projects in headless Chrome and check what the page draws (dev and check.py --render).

    python3 render_check.py <page_url> [<page_url>…] [--json]

For each page: waits for the readiness line the override sets (<html data-axis-map="ready">, window.__AXIS_MAP__),
then reads the engine, the project sha256 the page hashed, the click centre, and takes one screenshot. `crisp` is
the share of horizontally adjacent pixel pairs inside the map canvas that differ (any channel > 2): cells drawn as
squares by maplibre-gl-raster give a few per cent (cell edges only), the smoothing cog-tiler-wasm engine 15 % and
more. A raster page counts as crisp below CRISP_MAX. Chrome is found as $AXIS_CHROME, google-chrome, chromium or
chromium-browser (none: exit 3, nothing claimed). Stdlib plus Pillow (map env). Display check only.
Exit: 0 every page ready (and crisp when it has a raster layer) · 2 a page failed · 3 no Chrome.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True
CRISP_MAX = 0.06
READY_TIMEOUT_S = 45
NO_MARKER_S = 10
VIEWPORT = (1280, 960)


class NoChrome(Exception):
    pass


def find_chrome() -> str:
    win = []
    if os.name == "nt":   # not on PATH on Windows: the standard install folders (Chrome, then Edge, also Chromium)
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                win += [os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                        os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe")]
    for c in (os.environ.get("AXIS_CHROME"), "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", *win):
        if c and (shutil.which(c) or Path(c).is_file()):
            return shutil.which(c) or c
    raise NoChrome("no Chrome/Chromium found (set AXIS_CHROME)")


class WS:
    """Minimal RFC 6455 client (text frames, client masking) for the DevTools protocol."""

    def __init__(self, url: str, timeout: float = 60):
        u = urllib.parse.urlparse(url)
        self.s = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.s.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.s.recv(1 << 16)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text: str) -> None:
        data = text.encode()
        mask = os.urandom(4)
        n = len(data)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                               if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        parts = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            op = b0 & 0x0F
            if op == 8:
                raise ConnectionError("websocket closed by Chrome")
            if op in (0, 1, 2):
                parts.append(payload)
                if b0 & 0x80:
                    return b"".join(parts).decode()

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class Chrome:
    def __init__(self, tmp_root: Path | None = None):
        exe = find_chrome()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        if tmp_root is not None:
            Path(tmp_root).mkdir(parents=True, exist_ok=True)
        self.dir = tempfile.mkdtemp(prefix="axis-chrome-", dir=str(tmp_root) if tmp_root else None)
        self.p = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={self.dir}",
                                   "--no-first-run", "--no-default-browser-check", "--no-sandbox",
                                   "--enable-unsafe-swiftshader", "--use-angle=swiftshader",
                                   f"--window-size={VIEWPORT[0]},{VIEWPORT[1]}", "about:blank"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        tabs = None
        for _ in range(150):
            try:
                tabs = json.load(opener.open(f"http://127.0.0.1:{port}/json", timeout=2))
                break
            except Exception:
                time.sleep(0.1)
        if not tabs:
            self.close()
            raise NoChrome("Chrome did not start")
        page = next(t for t in tabs if t.get("type") == "page")
        self.ws = WS(page["webSocketDebuggerUrl"])
        self.i = 0

    def cmd(self, method: str, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.i:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result") or {}

    def js(self, expr: str):
        r = self.cmd("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return (r.get("result") or {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        try:
            self.p.terminate()
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()
        shutil.rmtree(self.dir, ignore_errors=True)


def crisp_ratio(png: bytes, rect: dict, below: float | None = None) -> float | None:
    """`below`: start under this page y (the caption box in the canvas corner is text, not cells)."""
    from PIL import Image
    import numpy as np
    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype("int16")
    x0, y0 = int(rect["x"] - rect["width"] / 2) + 8, int(rect["y"] - rect["height"] / 2) + 8
    if below is not None:
        y0 = max(y0, int(below) + 4)
    x1, y1 = int(rect["x"] + rect["width"] / 2) - 8, int(rect["y"] + rect["height"] / 2) - 60  # above the status line
    sub = a[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if sub.shape[0] < 10 or sub.shape[1] < 10:
        return None
    d = np.abs(np.diff(sub, axis=1)).max(axis=2)
    return round(float((d > 2).mean()), 4)


def check_page(ch: Chrome, url: str, shot: Path | None = None) -> dict:
    ch.cmd("Page.navigate", url=url)
    t0 = time.time()
    state = None
    while time.time() - t0 < READY_TIMEOUT_S:
        state = ch.js("document.documentElement.getAttribute('data-axis-map')")
        if state == "ready" or (state is None and time.time() - t0 > NO_MARKER_S):
            break                                # no override script: it never comes
        time.sleep(0.25)
    st = ch.js("JSON.stringify(window.__AXIS_MAP__ || null)")
    st = json.loads(st) if isinstance(st, str) else None
    res = {"url": url, "ready": state == "ready", "seconds": round(time.time() - t0, 1), "status": st,
           "status_line": ch.js("(document.getElementById('axis-map-status')||{}).textContent || null")}
    png = base64.b64decode(ch.cmd("Page.captureScreenshot", format="png")["data"])
    if shot:
        shot.write_bytes(png)
    if not st:
        res["problem"] = "no readiness marker: the override geolibre-runtime-config.js is not served"
        rect = ch.js("(function(){var c=document.querySelector('.maplibregl-canvas');if(!c)return null;"
                     "var r=c.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2,"
                     "width:r.width,height:r.height};})()")
        res["crisp"] = crisp_ratio(png, rect) if rect else None      # what the stock engine draws (smoothed)
        return res
    if st.get("rasterLayers"):
        below = ch.js("(function(){var c=document.getElementById('axis-map-caption');"
                      "return c ? c.getBoundingClientRect().bottom : null;})()")
        res["crisp"] = crisp_ratio(png, st["centre"], below) if st.get("centre") else None
        res["engine"] = st.get("engine")
        if res["engine"] != "maplibre-gl-raster":
            res["problem"] = f"raster engine is {res['engine']!r}, not maplibre-gl-raster"
        elif res["crisp"] is None or res["crisp"] > CRISP_MAX:
            res["problem"] = f"cells not drawn as squares (adjacent-pixel change {res['crisp']} > {CRISP_MAX})"
    if not res["ready"] and "problem" not in res:
        res["problem"] = f"not ready after {READY_TIMEOUT_S} s ({(st or {}).get('state')})"
    return res


def identify_page(url: str, calls: dict[str, list], shot: Path | None = None,
                  tmp_root: Path | None = None) -> dict:
    """Open the MapLibre page (mlview.py), wait for its ready line, run window.__AXIS_MAP__.identifyMany on each
    named list of [lon, lat] points: {"ready", "ready_line", "results": {name: [identify JSON, …]}}."""
    ch = Chrome(tmp_root)
    try:
        ch.cmd("Page.enable")
        ch.cmd("Runtime.enable")
        ch.cmd("Page.navigate", url=url)
        t0 = time.time()
        state = None
        while time.time() - t0 < READY_TIMEOUT_S:
            state = ch.js("document.documentElement.dataset.axisMap || null")
            if state == "ready":
                break
            time.sleep(0.25)
        out = {"ready": state == "ready", "seconds": round(time.time() - t0, 1),
               "ready_line": ch.js("(document.getElementById('ready')||{}).textContent || null"), "results": {}}
        for name, pts in calls.items():
            out["results"][name] = json.loads(ch.js(f"JSON.stringify(window.__AXIS_MAP__.identifyMany({json.dumps(pts)}))"))
        if shot:
            shot.write_bytes(base64.b64decode(ch.cmd("Page.captureScreenshot", format="png")["data"]))
        return out
    finally:
        ch.close()


def check_pages(urls: list[str], shots: Path | None = None, tmp_root: Path | None = None) -> list[dict]:
    ch = Chrome(tmp_root)
    try:
        ch.cmd("Page.enable")
        ch.cmd("Runtime.enable")
        out = []
        for i, u in enumerate(urls):
            out.append(check_page(ch, u, (shots / f"render-{i:02d}.png") if shots else None))
        return out
    finally:
        ch.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Headless render check of GeoLibre view pages (engine, crisp cells).")
    ap.add_argument("url", nargs="+")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        res = check_pages(a.url)
    except NoChrome as e:
        print(f"render_check.py: {e}", file=sys.stderr)
        return 3
    print(json.dumps(res, indent=1, sort_keys=True) if a.json else
          "\n".join(f"{'OK ' if 'problem' not in r else 'BAD'} {r['url']}: {r.get('problem') or r['status_line']}"
                    for r in res))
    return 0 if all("problem" not in r for r in res) else 2


if __name__ == "__main__":
    sys.exit(main())
