"""
utils/config_manager.py
Discord /config 커맨드에서 입력한 API 키를 .env에 저장.
OpenRouter 통합 이후 개별 LLM 제공자 키는 선택 사항으로만 유지.
"""

import os
import re
from pathlib import Path

_ENV_PATH = Path(__file__).parent.parent / ".env"

# key: (group, desc, editable)
# editable=False → 전용 커맨드로만 변경 가능 (예: 채널 ID, 봇 토큰) → 빠른 수정 Select 목록에서 제외.
_MANAGED_KEYS: dict[str, tuple[str, str, bool]] = {
    # ── AI / 외부 API (/config_ai 대상) ─────────────────────────
    "OPENROUTER_API_KEY":    ("AI", "OpenRouter — 통합 LLM 게이트웨이 (필수)", True),
    "PERPLEXITY_API_KEY":    ("AI", "Perplexity — 분쵸 리서치 (선택, OpenRouter로 대체 가능)", True),
    "YOUTUBE_API_KEY":       ("AI", "YouTube Data API v3 키 (선택)", True),
    "GITHUB_TOKEN":          ("AI", "GitHub PAT — 코드 변경 PR 생성 (선택)", True),

    # ── Notion ────────────────────────────────────────────────
    "NOTION_TOKEN":            ("Notion", "Notion API 토큰", True),
    "NOTION_STREAMERS_DB":     ("Notion", "스트리머 DB ID", True),
    "NOTION_BROADCAST_LOG_DB": ("Notion", "방송 로그 DB ID", True),
    "NOTION_REPORT_DB":        ("Notion", "리포트 DB ID", True),
    "NOTION_SCHEDULE_DB":      ("Notion", "스케줄 DB ID", True),
    "NOTION_FIXED_COSTS_DB":   ("Notion", "고정비 DB ID (선택, 미설정 시 로컬 JSON만 사용)", True),

    # ── Discord ──────────────────────────────────────────────
    "DISCORD_TOKEN":        ("Discord", "Discord 봇 토큰 (필수, 재시작 필요해 전용 커맨드 없음)", False),
    "CHO_USER_ID":          ("Discord", "오퍼레이터 유저 ID (필수)", True),
    "LOG_RAW_CHANNEL_ID":   ("Discord", "Raw Data 트레이스 채널 ID (전용: /rawdata_channel)", False),
    "FORUM_CHANNEL_ID":     ("Discord", "포럼 채널 ID (전용: /forum_channel)", False),
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
    for key, (group, desc, editable) in _MANAGED_KEYS.items():
        val = os.getenv(key, "")
        result[key] = {
            "group": group,
            "desc": desc,
            "editable": editable,
            "set": bool(val),
            "masked": f"{val[:8]}{'*' * 8}" if val else "미설정",
        }
    return result


def get_editable_keys(group: str | None = None) -> dict[str, dict]:
    """빠른 수정 Select 메뉴에 노출할 키만 (전용 커맨드가 있는 키는 제외)."""
    status = get_status()
    return {
        k: v for k, v in status.items()
        if v["editable"] and (group is None or v["group"] == group)
    }


def get_missing_keys() -> dict[str, dict]:
    """미설정된 키 전체 (그룹 무관) — 재시작 알림 등에서 사용."""
    return {k: v for k, v in get_status().items() if not v["set"]}


def mask(value: str | None) -> str:
    if not value:
        return "❌ 미설정"
    return f"✅ `{value[:8]}{'*' * 8}`"