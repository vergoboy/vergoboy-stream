import os, sys, json, urllib.request, urllib.error

env = {}
with open("/opt/stream/.stream_db.env") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
os.environ.update(env)
sys.path.insert(0, "/opt/stream")
import db as dbmod

admin_id = "cceb5d42-a143-4d08-a634-4fa510507fab"
token = dbmod.create_access_token(admin_id)
req = urllib.request.Request(
    "http://127.0.0.1:8801/stream/api/state",
    headers={"Authorization": f"Bearer {token}"},
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    print(json.dumps(data, indent=2)[:3000])
except urllib.error.HTTPError as e:
    print("HTTPERR:", e.code, e.read().decode())
