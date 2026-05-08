"""
utils/config_manager.py
Discord /config 커맨드에서 입력한 API 키를 .env에 저장.
OpenRouter 통합 이후 개별 LLM 제공자 키는 선택 사항으로만 유지.
"""

import os
import re
from pathlib import Path

_ENV_PATH = Path(__file__).parent.parent / ".env"

_MANAGED_KEYS = {
    # ── AI (필수) ─────────────────────────────────────────────
    "OPENROUTER_API_KEY":    ("AI", "OpenRouter — 통합 LLM 게이트웨이 (필수)"),
    "PERPLEXITY_API_KEY":    ("AI", "Perplexity — 분쵸 리서치 (선택, OpenRouter로 대체 가능)"),

    # ── YouTube (YouTube Analytics 연동 필요) ─────────────────
    "YOUTUBE_API_KEY":       ("YouTube", "YouTube Data API v3 키"),

    # ── Notion ────────────────────────────────────────────────
    "NOTION_TOKEN":            ("Notion", "Notion API 토큰"),
    "NOTION_STREAMERS_DB":     ("Notion", "스트리머 DB ID"),
    "NOTION_BROADCAST_LOG_DB": ("Notion", "방송 로그 DB ID"),
    "NOTION_REPORT_DB":        ("Notion", "리포트 DB ID"),
    "NOTION_SCHEDULE_DB":      ("Notion", "스케줄 DB ID"),

    # ── Discord ──────────────────────────────────────────────
    "DISCORD_TOKEN":        ("Discord", "Discord 봇 토큰 (필수)"),
    "CHO_USER_ID":          ("Discord", "오퍼레이터 유저 ID (필수)"),
    "LOG_RAW_CHANNEL_ID":   ("Discord", "Raw Data 트레이스 기록 채널 ID"),
    "FORUM_CHANNEL_ID":     ("Discord", "포럼 채널 ID (해쵸 세션 기록용)"),
}


def set_key(key: str, value: str) -> None:
    os.environ[key] = value
    try:
        _write_to_env_file(key, value)
    except OSError:
        # Railway 읽기전용 FS → 메모리 반영만
        pass


def _write_to_env_file(key: str, value: str) -> None:
    content = _ENV_PATH.read_text(encoding="utf-8") if _ENV_PATH.exists() else ""
    pattern = re.compile(rf'^{re.escape(key)}\s*=.*$', re.MULTILINE)
    new_line = f'{key}={value}'
    if pattern.search(content):
        content = pattern.sub(new_line, content)
    else:
        content = content.rstrip("\n") + f"\n{new_line}\n"
    _ENV_PATH.write_text(content, encoding="utf-8")


def get_status() -> dict[str, dict]:
    result = {}
    for key, (group, desc) in _MANAGED_KEYS.items():
        val = os.getenv(key, "")
        result[key] = {
            "group": group,
            "desc": desc,
            "set": bool(val),
            "masked": f"{val[:8]}{'*' * 8}" if val else "미설정",
        }
    return result


def mask(value: str | None) -> str:
    if not value:
        return "❌ 미설정"
    return f"✅ `{value[:8]}{'*' * 8}`"