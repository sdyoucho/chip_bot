"""
utils/model_config.py
Discord 커맨드로 변경한 모델 티어링을 JSON 파일로 영속화.
Railway Volume(/data) 우선, 없으면 로컬 ./data/ 폴백.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_BASE = Path("/data") if Path("/data").exists() else Path("./data")
_BASE.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = _BASE / "model_config.json"


def _read() -> dict:
    if not CONFIG_FILE.exists():
        return {"tiers": {}, "agents": {}}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"model_config.json 읽기 실패: {e}")
        return {"tiers": {}, "agents": {}}


def _write(data: dict) -> None:
    try:
        CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        log.warning(f"model_config.json 쓰기 실패 (읽기전용 FS일 수 있음): {e}")


def load_overrides() -> dict:
    """부팅 시 호출: 저장된 오버라이드 반환."""
    return _read()


def save_tier_override(tier: str, model: str) -> None:
    data = _read()
    data.setdefault("tiers", {})[tier] = model
    _write(data)


def save_agent_override(agent: str, tier: str) -> None:
    data = _read()
    data.setdefault("agents", {})[agent] = tier
    _write(data)


def reset_overrides() -> None:
    """모든 오버라이드 제거 → 기본값 복귀 (재부팅 필요)."""
    if CONFIG_FILE.exists():
        CONFIG_FILE.unlink()