"""
utils/json_store.py
공통 JSON 파일 영속화 헬퍼.
Railway Volume(/data) 우선, 없으면 로컬 ./data/ 폴백.

여러 모듈(credit_config, model_config, fixed_costs 등)이 거의 동일한
읽기/쓰기/기본값 폴백 보일러플레이트를 가지고 있어 공통화함.
"""

import json
import logging
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

_BASE = Path("/data") if Path("/data").exists() else Path("./data")
_BASE.mkdir(parents=True, exist_ok=True)


def store_path(filename: str) -> Path:
    """저장 파일의 전체 경로 (Railway Volume 우선)."""
    return _BASE / filename


def read_json(path: Path, default_factory: Callable[[], object]):
    """
    JSON 파일을 읽되, 없거나 손상됐으면 default_factory()를 호출해 반환.
    default_factory는 호출마다 새 객체를 만들어야 함 (가변 기본값 공유 방지).
    """
    if not path.exists():
        return default_factory()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"{path.name} 읽기 실패: {e}")
        return default_factory()


def write_json(path: Path, data: object) -> None:
    """JSON으로 직렬화해 저장. 읽기전용 FS 등 쓰기 실패는 경고만 남기고 무시."""
    try:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        log.warning(f"{path.name} 쓰기 실패 (읽기전용 FS일 수 있음): {e}")
