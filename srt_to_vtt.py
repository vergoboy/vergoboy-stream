"""
تبدیل ساده‌ی فایل زیرنویس srt به vtt (بدون نیاز به ffmpeg یا کتابخانه خارجی).
- کاما در تایم‌استمپ‌ها به نقطه تبدیل می‌شود (الزام فرمت vtt)
- خطوط شماره‌ترتیب (که قبل از خط زمان‌بندی می‌آیند) حذف می‌شوند
- هدر WEBVTT اضافه می‌شود
"""
import re


def convert_srt_to_vtt(srt_path: str, vtt_path: str) -> None:
    with open(srt_path, encoding="utf-8", errors="ignore") as f:
        content = f.read()

    content = content.replace("\r\n", "\n").replace("\r", "\n")
    # 00:00:01,234 --> 00:00:02,000   =>   00:00:01.234 --> 00:00:02.000
    content = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", content)

    lines = content.split("\n")
    out = ["WEBVTT", ""]
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        # خط شماره‌ی صرف که بلافاصله قبل از خط زمان‌بندی است را رد کن
        if re.fullmatch(r"\d+", stripped) and i + 1 < n and "-->" in lines[i + 1]:
            i += 1
            continue
        out.append(line)
        i += 1

    with open(vtt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).strip() + "\n")
