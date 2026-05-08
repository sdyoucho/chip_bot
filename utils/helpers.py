"""utils/helpers.py — 공통 유틸 함수."""

from datetime import datetime
import pytz

KST = pytz.timezone("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(KST)


def fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def fmt_datetime(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def truncate(text: str, max_len: int = 1024) -> str:
    return text[:max_len - 3] + "..." if len(text) > max_len else text


def format_won(amount: int) -> str:
    return f"₩{amount:,}"
