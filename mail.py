"""Outgoing verification mail, delivered through the server's own postfix
(listening on 127.0.0.1:25) from the info@vergoboy.ir mailbox. No external
SMTP credentials are needed. Falls back to the system `sendmail` binary if
the SMTP connection fails for any reason."""
import smtplib
import subprocess
from email.headerregistry import Address
from email.message import EmailMessage

from config import Config


def _build_message(to: str, subject: str, html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = Address("Watch Party", "info", "vergoboy.ir")
    msg["To"] = to
    msg.set_content("مشاهده پیام در مرورگر.")
    msg.add_alternative(html, subtype="html")
    return msg


def send_email(to: str, subject: str, html: str) -> bool:
    """Sends an HTML email to `to`. Returns True on success."""
    if not to or "@" not in to:
        return False
    msg = _build_message(to, subject, html)
    try:
        with smtplib.SMTP("127.0.0.1", 25, timeout=15) as srv:
            srv.send_message(msg)
        return True
    except Exception as e:
        print(f"[mail] smtp error ({e}); falling back to sendmail")
    try:
        proc = subprocess.run(
            ["/usr/sbin/sendmail", "-t", "-f", Config.MAIL_FROM],
            input=msg.as_bytes(),
            capture_output=True,
            timeout=30,
        )
        return proc.returncode == 0
    except Exception as e:
        print(f"[mail] sendmail error: {e}")
        return False


def send_verification_email(to: str, verify_url: str) -> bool:
    subject = "تأیید ایمیل حساب — vergoboy"
    html = f"""\
<html dir="rtl" lang="fa"><body style="margin:0;padding:0;background:#f2f0ea;font-family:Tahoma,Arial,sans-serif">
  <div style="max-width:520px;margin:24px auto;background:#fff;border-radius:18px;overflow:hidden;border:1px solid #e6e2d8">
    <div style="background:linear-gradient(135deg,#d4a017,#6b4e93);padding:22px 28px">
      <div style="font-size:22px;font-weight:bold;color:#fff">تماشای مشترک · vergoboy</div>
    </div>
    <div style="padding:28px">
      <p style="margin:0 0 14px;font-size:15px;color:#2b2b2b;line-height:1.9">سلام،</p>
      <p style="margin:0 0 22px;font-size:14px;color:#444;line-height:1.9">
        برای تأیید ایمیل حساب‌ات روی دکمه زیر بزن. این لینک تا ۷۲ ساعت معتبر است.
      </p>
      <a href="{verify_url}" style="display:inline-block;background:linear-gradient(135deg,#d4a017,#6b4e93);color:#fff;text-decoration:none;font-size:15px;font-weight:bold;padding:13px 34px;border-radius:12px">
        تأیید ایمیل
      </a>
      <p style="margin:24px 0 0;font-size:12px;color:#8a8578;line-height:1.8">
        اگر این ایمیل را تو نفرستاده‌ای، آن را نادیده بگیر.<br>
        لینک مستقیم: <a href="{verify_url}" style="color:#6b4e93">{verify_url}</a>
      </p>
    </div>
  </div>
</body></html>"""
    return send_email(to, subject, html)
