"""
utils/credit_config.py
인쵸 크레딧 임계치 알림 설정 — 월 한도(USD) + 알림 퍼센트를 JSON으로 영속화.
Railway Volume(/data) 우선, 없으면 로컬 ./data/ 폴백.

기존에는 OpenRouter 계정의 전체(누적) 크레딧 대비 사용률로 알림을 보내서
- 계정을 충전한 이후로는 계속 70%대를 유지해 재시작마다 50%/70%가 동시 발송되고
- 월별로 리셋되지 않는 문제가 있었음.
여기서는 이번 달 실사용액(cost_tracker.get_monthly_total)을 월 한도로 나눈
비율을 기준으로 삼고, 알림 발송 여부도 "YYYY-MM" 단위로 영속화해 재시작에도
같은 달에는 같은 임계치를 두 번 보내지 않도록 한다.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_BASE = Path("/data") if Path("/data").exists() else Path("./data")
_BASE.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = _BASE / "credit_config.json"

_DEFAULTS = {
    "monthly_limit": 50.0,           # USD
    "thresholds": [0.5, 0.7, 1.0],   # 50% / 70% / 100%
    "alerted": {},                   # {"YYYY-MM": [0.5, 0.7, ...]}
}


def _read() -> dict:
    if not CONFIG_FILE.exists():
        return {**_DEFAULTS, "alerted": {}}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return {
            "monthly_limit": float(data.get("monthly_limit", _DEFAULTS["monthly_limit"])),
            "thresholds": sorted(float(t) for t in data.get("thresholds", _DEFAULTS["thresholds"])),
            "alerted": data.get("alerted", {}),
        }
    except (json.JSONDecodeError, OSError, TypeError, ValueError) as e:
        log.warning(f"credit_config.json 읽기 실패: {e}")
        return {**_DEFAULTS, "alerted": {}}


def _write(data: dict) -> None:
    try:
        CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        log.warning(f"credit_config.json 쓰기 실패 (읽기전용 FS일 수 있음): {e}")


def get_monthly_limit() -> float:
    return _read()["monthly_limit"]


def set_monthly_limit(amount: float) -> None:
    if amount <= 0:
        raise ValueError("월 한도는 0보다 커야 합니다.")
    data = _read()
    data["monthly_limit"] = round(float(amount), 4)
    _write(data)


def get_thresholds() -> list[float]:
    return _read()["thresholds"]


def set_thresholds(thresholds: list[float]) -> None:
    cleaned = sorted({round(float(t), 4) for t in thresholds})
    if not cleaned or any(t <= 0 for t in cleaned):
        raise ValueError("임계치는 0보다 큰 값이어야 합니다.")
    data = _read()
    data["thresholds"] = cleaned
    _write(data)


def is_alerted(month_key: str, threshold: float) -> bool:
    alerted = _read()["alerted"]
    return round(float(threshold), 4) in alerted.get(month_key, [])


def mark_alerted(month_key: str, threshold: float) -> None:
    data = _read()
    months = data.setdefault("alerted", {})
    sent = months.setdefault(month_key, [])
    t = round(float(threshold), 4)
    if t not in sent:
        sent.append(t)
    # 오래된 달의 기록은 정리 (현재 달만 유지)
    data["alerted"] = {month_key: sent}
    _write(data)
