"""serve.py: the GeoLibre bundle and a data folder over HTTP Range, hardened /data/ and override paths."""
from __future__ import annotations

import http.client
import os
import threading
from pathlib import Path

import numpy as np
import pytest

import mapenv
import serve
from conftest import map_python


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    map_python()
    app = mapenv.app_dir()
    root = tmp_path_factory.mktemp("srv")
    data = root / "migration"
    (data / "runs" / "r1").mkdir(parents=True)
    import rasterio
    import rasterio.shutil
    from rasterio.transform import from_origin
    a = (np.arange(300 * 300, dtype="float32") % 251).reshape(300, 300)
    with rasterio.open(root / "plain.tif", "w", driver="GTiff", width=300, height=300, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(6.0, 50.0, 0.001, 0.001), nodata=-1) as ds:
        ds.write(a, 1)
    rasterio.shutil.copy(str(root / "plain.tif"), str(data / "runs" / "r1" / "a cog.tif"), driver="COG")
    (root / "secret.txt").write_text("outside the data root\n")
    os.symlink(root / "secret.txt", data / "link-out.txt")
    (data / "sub").mkdir()
    (data / "sub" / "x.json").write_text('{"a": 1}\n')
    ovr = root / "override"
    ovr.mkdir()
    (ovr / "deployment.json").write_text('{"version": 1}\n')
    (ovr / "only-override.json").write_text("[]\n")
    srv = serve.make_server(str(app), str(data), 0, str(ovr))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]
    yield {"port": port, "data": data, "app": Path(app), "root": root,
           "cog": (data / "runs" / "r1" / "a cog.tif").read_bytes()}
    srv.shutdown()
    srv.server_close()


def req(server, path, headers=None, method="GET", host=None):
    c = http.client.HTTPConnection("127.0.0.1", server["port"], timeout=10)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)   # raw path: no client-side normalising
    c.putheader("Host", host or f"127.0.0.1:{server['port']}")
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    c.endheaders()
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, body


COG = "/data/runs/r1/a%20cog.tif"


def test_binds_loopback_only(server):
    srv = serve.make_server(str(server["app"]), str(server["data"]), 0)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


def test_app_index_and_override(server):
    code, h, body = req(server, "/")
    assert code == 200 and b"<html" in body.lower()
    code, _, body = req(server, "/deployment.json")
    assert code == 200 and body == b'{"version": 1}\n'                   # the override shadows the bundle file
    code, _, body = req(server, "/only-override.json")
    assert code == 200 and body == b"[]\n"
    code, _, _ = req(server, "/assets/")
    assert code == 404                                                    # no directory listings


def test_range_206_exact_bytes(server):
    cog = server["cog"]
    code, h, body = req(server, COG, {"Range": "bytes=0-16383"})
    assert code == 206 and body == cog[:16384]
    assert h["content-range"] == f"bytes 0-16383/{len(cog)}" and h["content-length"] == "16384"
    assert h["accept-ranges"] == "bytes" and h["content-type"] == "image/tiff"
    code, h, body = req(server, COG, {"Range": "bytes=1000-1999"})
    assert code == 206 and body == cog[1000:2000]
    code, h, body = req(server, COG, {"Range": "bytes=-500"})
    assert code == 206 and body == cog[-500:]
    code, h, body = req(server, COG, {"Range": f"bytes={len(cog) - 10}-"})
    assert code == 206 and body == cog[-10:]
    code, h, body = req(server, COG, {"Range": f"bytes=0-{len(cog) + 999}"})
    assert code == 206 and body == cog and h["content-range"] == f"bytes 0-{len(cog) - 1}/{len(cog)}"
    code, h, _ = req(server, COG, {"Range": f"bytes={len(cog)}-"})
    assert code == 416 and h["content-range"] == f"bytes */{len(cog)}"
    code, h, body = req(server, COG)
    assert code == 200 and body == cog and h["cache-control"] == "no-store"
    code, h, body = req(server, COG, {"Range": "bytes=0-99"}, method="HEAD")
    assert code == 206 and body == b"" and h["content-length"] == "100"


def test_range_on_the_app_bundle(server):
    full = (server["app"] / "index.html").read_bytes()
    code, h, body = req(server, "/index.html", {"Range": "bytes=0-49"})
    assert code == 206 and body == full[:50]


@pytest.mark.parametrize("path", [
    "/data/../secret.txt", "/data/../../etc/passwd", "/data/runs/../../secret.txt", "/data/%2e%2e/secret.txt",
    "/data/%2E%2E/%2E%2E/etc/passwd", "/data/runs/..%2f..%2fsecret.txt", "/data/%2e%2e%2fsecret.txt",
    "/data/sub/%2e%2e/%2e%2e/secret.txt", "/data//etc/passwd", "/data/%2Fetc%2Fpasswd", "/data/x%00.json",
    "/data/link-out.txt", "/data/", "/data", "/data/..\\secret.txt",
])
def test_traversal_refused(server, path):
    code, _, body = req(server, path)
    assert code in (400, 403), (path, code)
    assert b"outside the data root" not in body and b"root:" not in body


def test_inside_paths_still_served(server):
    code, _, body = req(server, "/data/sub/x.json")
    assert code == 200 and body == b'{"a": 1}\n'
    code, _, body = req(server, "/data/sub/./x.json")
    assert code == 200
    code, _, _ = req(server, "/data/sub/missing.json")
    assert code == 404
    code, _, _ = req(server, "/data/sub/")
    assert code == 404                                                    # a folder is not a file


def test_override_cannot_escape(server):
    code, _, body = req(server, "/%2e%2e/secret.txt")
    assert b"outside the data root" not in body
    code, _, body = req(server, "/../secret.txt")
    assert b"outside the data root" not in body


def test_foreign_host_refused(server):
    code, _, _ = req(server, COG, host=f"evil.example:{server['port']}")
    assert code == 403
    code, _, _ = req(server, COG, host=f"localhost:{server['port']}")
    assert code == 200


def test_cors_only_own_origin(server):
    own = f"http://127.0.0.1:{server['port']}"
    _, h, _ = req(server, COG, {"Origin": own, "Range": "bytes=0-9"})
    assert h.get("access-control-allow-origin") == own and "Content-Range" in h["access-control-expose-headers"]
    _, h, _ = req(server, COG, {"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in h
