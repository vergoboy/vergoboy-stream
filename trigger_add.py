"""One-off: POST an add-url job to the running instance.

Diagnostic/manual helper, not part of the app. Like get_state.py it targets the
instance it is run from — the database env file and import path come from this
script's own directory, the port and mount point come from ``config`` — so it
works against either deployment without editing (override with
``STREAM_INSTANCE_ROOT`` if run from elsewhere).
"""
import os, sys, json
ROOT = os.environ.get("STREAM_INSTANCE_ROOT") or os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("STREAM_HOST", "127.0.0.1")
os.environ.setdefault("STREAM_PORT", "8801")
try:
    env = {}
    with open(os.path.join(ROOT, ".stream_db.env")) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    os.environ.update(env)
except Exception as e:
    print("env load warn:", e)

sys.path.insert(0, ROOT)
import db as dbmod
from sqlalchemy import text

API = f"http://127.0.0.1:{dbmod.Config.PORT}{dbmod.Config.BASE_PATH}/api"

admin_id = None
s = dbmod.SessionLocal()
try:
    rows = s.execute(text("SELECT id, username, role, current_room_id FROM users WHERE role='admin'")).all()
    for r in rows:
        print("ADMIN CANDIDATE:", dict(r._mapping))
    from sqlalchemy import create_engine
except Exception as e:
    pass
finally:
    s.close()

import urllib.request, urllib.parse

folder_url = "https://vergoboy.ir/files/data/Red.White.and.Royal.Blue.2023.1080p.WEBRip.1400MB.DD5.1.x264-GalaxyRG%5BTGx%5D"

s = dbmod.SessionLocal()
try:
    rows = s.execute(text("SELECT id FROM users WHERE role='admin' LIMIT 1")).all()
    admin_id = rows[0][0]
finally:
    s.close()

token = dbmod.create_access_token(admin_id)
print("TOKEN:", token[:20], "len", len(token))

req = urllib.request.Request(
    f"{API}/add-url",
    data=urllib.parse.urlencode({}).encode(),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    method="POST",
)
body = json.dumps({"url": folder_url, "title": "Red White and Royal Blue (2023) 1080p", "name": "vergoboy"}).encode()
req = urllib.request.Request(
    f"{API}/add-url",
    data=body,
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        print("STATUS:", resp.status)
        print("RESP:", resp.read().decode())
except urllib.error.HTTPError as e:
    print("HTTPERR:", e.code, e.read().decode())
