"""
utils/model_config.py
Discord 커맨드로 변경한 모델 티어링을 JSON 파일로 영속화.
"""

from utils.json_store import store_path, read_json, write_json

CONFIG_FILE = store_path("model_config.json")


def _read() -> dict:
    return read_json(CONFIG_FILE, lambda: {"tiers": {}, "agents": {}})


def _write(data: dict) -> None:
    write_json(CONFIG_FILE, data)


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