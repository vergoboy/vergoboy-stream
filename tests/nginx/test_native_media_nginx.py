"""Runs a REAL nginx with deploy/nginx-native-media.conf and checks what an attacker/friend/player gets.
Skipped when nginx (with http_secure_link_module) is not installed."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import native_media as nm  # noqa: E402

NGINX = shutil.which("nginx") or "/usr/sbin/nginx"
SECRET = "n" * 40
BASE = "/w2g/native/uploads"
BLOB = bytes(range(256)) * 40  # 10 240 bytes with a recognisable pattern

pytestmark = pytest.mark.skipif(
    not os.path.exists(NGINX) or b"secure_link" not in subprocess.run([NGINX, "-V"], capture_output=True).stderr,
    reason="nginx with http_secure_link_module not installed",
)


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture(scope="module")
def site():
    d = tempfile.mkdtemp(prefix="w2g-nginx-")
    os.chmod(d, 0o755)  # mkdtemp is 0700; nginx workers run as another user and must traverse it
    for sub in ("media/uploads", "media/hls/it1", "logs", "tmp"):
        os.makedirs(f"{d}/{sub}")
    open(f"{d}/media/uploads/it1_src.mkv", "wb").write(BLOB)
    open(f"{d}/media/hls/it1/master.m3u8", "w").write("#EXTM3U\n")
    for root, dirs, files in os.walk(d):
        for x in dirs: os.chmod(os.path.join(root, x), 0o755)
        for x in files: os.chmod(os.path.join(root, x), 0o644)
    open(f"{d}/secret.conf", "w").write(f'set $w2g_media_secret "{SECRET}";\n')
    snippet = open(f"{ROOT}/deploy/nginx-native-media.conf").read()
    snippet = snippet.replace("/etc/nginx/w2g-media-secret.conf", f"{d}/secret.conf").replace("/opt/w2g/media/", f"{d}/media/")
    port = _free_port()
    conf = f"""
daemon off; pid {d}/nginx.pid; error_log {d}/logs/error.log info;
events {{ worker_connections 64; }}
http {{
  access_log off;
  client_body_temp_path {d}/tmp/b; proxy_temp_path {d}/tmp/p; fastcgi_temp_path {d}/tmp/f; uwsgi_temp_path {d}/tmp/u; scgi_temp_path {d}/tmp/s;
  server {{
    listen 127.0.0.1:{port};
    # same order/shape as production: the generic media alias exists and the snippet must override it
    location /w2g/media/ {{ alias {d}/media/; }}
    {snippet}
  }}
}}
"""
    open(f"{d}/nginx.conf", "w").write(conf)
    test = subprocess.run([NGINX, "-t", "-c", f"{d}/nginx.conf", "-p", d], capture_output=True, text=True)
    assert test.returncode == 0, test.stderr
    p = subprocess.Popen([NGINX, "-c", f"{d}/nginx.conf", "-p", d], stderr=subprocess.DEVNULL)
    for _ in range(50):
        try: socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
        except OSError: time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    p.terminate(); p.wait(timeout=10); shutil.rmtree(d, ignore_errors=True)


def get(url, headers=None):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=5)
        return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def link(site, name="it1_src.mkv", user="u-1", exp=None, secret=SECRET):
    exp = exp or int(time.time()) + 3600
    return site + nm.signed_path(secret, BASE, name, user, exp)


def test_valid_link_serves_the_original_bytes(site):
    status, body, h = get(link(site))
    assert (status, body) == (200, BLOB)
    assert "no-store" in h.get("Cache-Control", "")


def test_range_requests_work_so_seeking_is_cheap(site):
    status, body, h = get(link(site), {"Range": "bytes=1000-1999"})
    assert status == 206 and body == BLOB[1000:2000]
    assert h["Content-Range"] == f"bytes 1000-1999/{len(BLOB)}"
    status, body, _ = get(link(site), {"Range": "bytes=-16"})   # tail read (mkv/mp4 index at the end)
    assert status == 206 and body == BLOB[-16:]


def test_expired_link_is_410(site):
    assert get(link(site, exp=int(time.time()) - 5))[0] == 410


def test_wrong_secret_other_user_other_file_and_tampering_are_403(site):
    good = link(site)
    assert get(link(site, secret="x" * 40))[0] == 403
    assert get(good.replace("u=u-1", "u=u-2"))[0] == 403            # token handed to someone else
    other = good.replace("it1_src.mkv", "it2_src.mkv")                 # signature of another file
    assert get(other)[0] == 403
    assert get(good.replace("exp=", "exp=9"))[0] == 403            # extended expiry
    assert get(good.split("&md5=")[0])[0] == 403                    # signature removed
    assert get(site + BASE + "/it1_src.mkv")[0] == 403              # no query at all


def test_path_traversal_does_not_escape(site):
    for evil in ("/..%2fhls/it1/master.m3u8", "/%2e%2e/hls/it1/master.m3u8"):
        assert get(site + BASE + evil + "?exp=4102444800&u=u-1&md5=abc")[0] in (400, 403, 404)


def test_the_public_raw_upload_hole_is_closed(site):
    assert get(site + "/w2g/media/uploads/it1_src.mkv")[0] == 404


def test_hls_and_other_media_are_unchanged(site):
    status, body, _ = get(site + "/w2g/media/hls/it1/master.m3u8")
    assert (status, body) == (200, b"#EXTM3U\n")
