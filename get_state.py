"""Print the running instance's ``/api/state`` as JSON.

Diagnostic helper. It targets the instance it is *run from*: the database env
file and the import path come from this script's own directory, and the port
and mount point come from ``config``. The same file therefore works unchanged
for the original instance (``/opt/stream``) and the second one (``/opt/w2g``);
override the directory with ``STREAM_INSTANCE_ROOT`` if the script is run from
somewhere else.
"""
import os, sys, json, urllib.request, urllib.error

ROOT = os.environ.get("STREAM_INSTANCE_ROOT") or os.path.dirname(os.path.abspath(__file__))

env = {}
with open(os.path.join(ROOT, ".stream_db.env")) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
os.environ.update(env)
sys.path.insert(0, ROOT)
import db as dbmod

admin_id = "cceb5d42-a143-4d08-a634-4fa510507fab"
token = dbmod.create_access_token(admin_id)
req = urllib.request.Request(
    f"http://127.0.0.1:{dbmod.Config.PORT}{dbmod.Config.BASE_PATH}/api/state",
    headers={"Authorization": f"Bearer {token}"},
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    print(json.dumps(data, indent=2)[:3000])
except urllib.error.HTTPError as e:
    print("HTTPERR:", e.code, e.read().decode())
