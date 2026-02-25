"""
توابع کمکی عمومی
"""

import base64
from datetime import datetime


def decode_base64_content(encoded: str) -> str:
    """دیکد محتوای base64"""
    try:
        return base64.b64decode(encoded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def format_timestamp(iso_string: str | None) -> str:
    """تبدیل تاریخ ISO به فرمت خوانا"""
    if not iso_string:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return iso_string


def truncate(text: str, max_length: int = 200) -> str:
    """کوتاه کردن متن طولانی"""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."