"""
utils/config_manager.py
Discord /config 커맨드에서 입력한 API 키를 .env에 저장하고
현재 프로세스에 즉시 반영.
"""

import os
import re
from pathlib import Path

_ENV_PATH = Path(__file__).parent.parent / ".env"

_MANAGED_KEYS = {
    # AI API
    "OPENROUTER_API_KEY":    ("AI", "OpenRouter — 통합 LLM 게이트웨이 (필수)"),
    "GEMINI_API_KEY":        ("AI", "Gemini — 레거시/폴백"),
    "ANTHROPIC_API_KEY":     ("AI", "Anthropic — 직접 호출용 (OpenRouter 대안)"),
    "OPENAI_API_KEY":        ("AI", "OpenAI — 직접 호출용"),
    "PERPLEXITY_API_KEY":    ("AI", "Perplexity — 분쵸 리서치"),
    # Notion (기존)
    "NOTION_TOKEN":            ("Notion", "Notion API 토큰"),
    "NOTION_STREAMERS_DB":     ("Notion", "스트리머 DB ID"),
    "NOTION_BROADCAST_LOG_DB": ("Notion", "방송 로그 DB ID"),
    "NOTION_REPORT_DB":        ("Notion", "리포트 DB ID"),
    "NOTION_SCHEDULE_DB":      ("Notion", "스케줄 DB ID"),
    # Discord
    "DISCORD_TOKEN":         ("Discord", "Discord 봇 토큰"),
    "DISCORD_GUILD_ID":      ("Discord", "서버 ID"),
    "CHO_USER_ID":           ("Discord", "오퍼레이터 유저 ID"),
    "LOG_RAW_CHANNEL_ID":    ("Discord", "Raw Data 트레이스 로그 채널 ID"),
    "FORUM_CHANNEL_ID":      ("Discord", "포럼 채널 ID (해쵸 세션 기록)"),
}


def set_key(key: str, value: str) -> None:
    """
    env 변수를 현재 프로세스에 즉시 반영.
    로컬에서는 .env 파일에도 저장. Railway 등 읽기전용 환경에서는 메모리만 반영.
    """
    os.environ[key] = value
    try:
        _write_to_env_file(key, value)
    except OSError:
        # Railway 등 읽기전용 파일시스템 — 프로세스 메모리에만 반영됨
        # Railway 환경에서 영구 저장은 Railway 대시보드 > Variables에서 설정
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
    """모든 관리 키의 설정 여부와 메타 정보 반환."""
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
