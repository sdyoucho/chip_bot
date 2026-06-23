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
_IS_PERSISTENT = _BASE == Path("/data")

if not _IS_PERSISTENT:
    log.warning(
        "/data 볼륨이 마운트되지 않아 ./data로 폴백 — Railway 등 컨테이너 배포에서는 "
        "재시작/재배포 시 크레딧 설정·고정비·모델 티어 등 이 저장소의 모든 값이 초기화됩니다. "
        "Railway 대시보드에서 Volume을 /data에 마운트하세요."
    )


def is_persistent() -> bool:
    """/data가 실제 마운트된 Volume인지 여부. False면 재시작 시 저장된 값이 모두 초기화됨."""
    return _IS_PERSISTENT


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
