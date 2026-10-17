#!/usr/bin/env python3
"""Send a real test email through the app's own mail path (mail.py → postfix).

This exercises exactly what password-reset and email-verification use:
`mail.send_email` builds the message (From: info@vergoboy.ir) and hands it to
the local postfix on 127.0.0.1:25; after that delivery is postfix's job, so
the definitive check afterwards is the mail log:

    sudo grep 'status=sent' /var/log/mail.log | tail -3

Run it as the service user, on the deploy host, with the instance's env file:

    sudo -u www-data python3 /opt/w2g/scripts/test_email.py

Defaults are the /w2g instance: env file /opt/w2g/.stream_db.env, recipient
STREAM_ADMIN_EMAIL from that file (very.good.booyy@gmail.com). Override with
--env-file / --to / --subject. The script exits 0 only when mail.send_email
returns True (postfix accepted the message), 1 otherwise.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ENV_FILE = "/opt/w2g/.stream_db.env"
DEFAULT_TO = "very.good.booyy@gmail.com"

# The unit's own Environment= lines (deploy/vergoboy-w2g.service). Loading them
# keeps Config's paths resolving exactly like the running service — without
# STREAM_INSTANCE_ROOT the makedirs in config.py would create media/data dirs
# relative to wherever this script happens to sit.
UNIT_DEFAULTS = {
    "STREAM_ENV": "production",
    "STREAM_HOST": "127.0.0.1",
    "STREAM_PORT": "8802",
    "STREAM_BASE_PATH": "/w2g",
    "STREAM_INSTANCE": "w2g",
    "STREAM_INSTANCE_ROOT": "/opt/w2g",
    "STREAM_SITE_URL": "https://vergoboy.ir/w2g",
}


def load_env_file(path: Path) -> None:
    """Import KEY=value lines into os.environ without overriding what is set."""
    if not path.is_file():
        raise SystemExit(f"env file not found: {path} (use --env-file)")
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)\Z", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2)


def resolve_app_dir(explicit: str | None) -> Path:
    """Where mail.py lives: --app-dir, else the release this script ships in,
    else the /w2g current symlink (for a copy placed outside the release)."""
    if explicit:
        return Path(explicit)
    here = Path(__file__).resolve().parent.parent
    if (here / "mail.py").is_file():
        return here
    fallback = Path("/opt/w2g/current")
    if (fallback / "mail.py").is_file():
        return fallback
    raise SystemExit("cannot find mail.py — pass --app-dir")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", default=DEFAULT_ENV_FILE,
                    help=f"instance env file (default: {DEFAULT_ENV_FILE})")
    ap.add_argument("--to", default=None,
                    help="recipient (default: STREAM_ADMIN_EMAIL from the env "
                         f"file, falling back to {DEFAULT_TO})")
    ap.add_argument("--subject", default="تست ارسال ایمیل — vergoboy")
    ap.add_argument("--app-dir", default=None,
                    help="directory containing mail.py/config.py")
    args = ap.parse_args()

    if os.geteuid() == 0:
        print("warning: running as root; the service runs as www-data — "
              "prefer: sudo -u www-data python3 ...", file=sys.stderr)

    load_env_file(Path(args.env_file))
    for k, v in UNIT_DEFAULTS.items():
        os.environ.setdefault(k, v)
    to = args.to or os.environ.get("STREAM_ADMIN_EMAIL", "").strip() or DEFAULT_TO

    app_dir = resolve_app_dir(args.app_dir)
    sys.path.insert(0, str(app_dir))

    import mail  # noqa: E402  (needs the env + sys.path set up first)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    html = f"""\
<html dir="rtl" lang="fa"><body style="margin:0;padding:0;background:#f2f0ea;font-family:Tahoma,Arial,sans-serif">
  <div style="max-width:520px;margin:24px auto;background:#fff;border-radius:18px;overflow:hidden;border:1px solid #e6e2d8">
    <div style="background:linear-gradient(135deg,#d4a017,#6b4e93);padding:22px 28px">
      <div style="font-size:22px;font-weight:bold;color:#fff">تست ایمیل · vergoboy</div>
    </div>
    <div style="padding:28px">
      <p style="margin:0 0 12px;font-size:15px;color:#2b2b2b;line-height:1.9">سلام،</p>
      <p style="margin:0 0 12px;font-size:14px;color:#444;line-height:1.9">
        این یک پیام آزمایشی است که از مسیر واقعی برنامه (mail.py → postfix محلی)
        ارسال شده تا سرویس ارسال ایمیل را بررسی کند.
      </p>
      <p style="margin:0;font-size:12px;color:#8a8578;line-height:1.8">
        زمان ارسال: {stamp} (UTC)<br>فرستنده: info@vergoboy.ir
      </p>
    </div>
  </div>
</body></html>"""

    print(f"app dir : {app_dir}")
    print(f"env file: {args.env_file}")
    print(f"from    : info@vergoboy.ir  (Config.MAIL_FROM = {mail.Config.MAIL_FROM})")
    print(f"to      : {to}")
    print("sending via mail.send_email → smtp 127.0.0.1:25 …")

    ok = mail.send_email(to, args.subject, html)
    if ok:
        print("RESULT: accepted by postfix (mail.send_email -> True).")
        print("verify delivery with:  sudo grep 'status=sent' /var/log/mail.log | tail -3")
        return 0
    print("RESULT: FAILED (mail.send_email -> False) — see any [mail] errors above.")
    print("check:  systemctl is-active postfix@-   and   ss -ltn | grep ':25 '")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
