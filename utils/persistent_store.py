"""영속 저장소 모듈.

봇 재부팅 후에도 유지되어야 하는 설정값(config_ai, rawdata_channel,
code_diagnose 토글, rawdata 활성화 상태 등)을 JSON 파일 기반으로
저장/로드하기 위한 헬퍼.

저장 위치: ``data/persistent/{namespace}.json``
"""

import json
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, Optional

# 영속 데이터 저장 루트 디렉토리
_BASE_DIR: Path = Path("data") / "persistent"
_BASE_DIR.mkdir(parents=True, exist_ok=True)

# namespace 단위 동시성 보호용 락
_locks_guard: threading.Lock = threading.Lock()
_locks: Dict[str, threading.Lock] = {}

# 메모리 캐시 (디스크 IO 최소화)
_cache: Dict[str, Dict[str, Any]] = {}

# log_raw_channel_id 전용 namespace/key
_LOG_RAW_NS: str = "log_channel"
_LOG_RAW_KEY: str = "log_raw_channel_id"


def _get_lock(namespace: str) -> threading.Lock:
    """namespace 전용 Lock을 반환 (없으면 생성)."""
    with _locks_guard:
        lock = _locks.get(namespace)
        if lock is None:
            lock = threading.Lock()
            _locks[namespace] = lock
        return lock


def _path_for(namespace: str) -> Path:
    """namespace에 해당하는 JSON 파일 경로 반환."""
    # 경로 인젝션 방지: 슬래시/역슬래시 제거
    safe = namespace.replace("/", "_").replace("\\", "_")
    return _BASE_DIR / f"{safe}.json"


def _backup_corrupt(path: Path) -> None:
    """손상된 파일을 .corrupt 확장자로 백업."""
    try:
        backup = path.with_suffix(path.suffix + ".corrupt")
        shutil.copy2(path, backup)
    except Exception:
        # 백업 실패는 무시 (원본 동작에 영향 주지 않음)
        pass


def load(namespace: str) -> Dict[str, Any]:
    """주어진 namespace의 전체 데이터를 dict로 로드.

    파일이 없거나 JSON 파싱에 실패하면 빈 dict를 반환하며,
    손상된 파일은 ``.corrupt`` 백업 후 무시한다.
    """
    lock = _get_lock(namespace)
    with lock:
        if namespace in _cache:
            # 캐시된 사본 반환 (외부에서 직접 수정해도 디스크 영향 없음)
            return dict(_cache[namespace])

        path = _path_for(namespace)
        if not path.exists():
            _cache[namespace] = {}
            return {}

        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                # 형식이 dict가 아니면 손상으로 간주
                _backup_corrupt(path)
                _cache[namespace] = {}
                return {}
            _cache[namespace] = data
            return dict(data)
        except (json.JSONDecodeError, OSError):
            _backup_corrupt(path)
            _cache[namespace] = {}
            return {}


def save(namespace: str, data: Dict[str, Any]) -> None:
    """namespace 전체 데이터를 디스크에 원자적으로 저장."""
    lock = _get_lock(namespace)
    with lock:
        path = _path_for(namespace)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(path)
            _cache[namespace] = dict(data)
        except OSError:
            # 임시 파일 정리
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise


def get(namespace: str, key: str, default: Any = None) -> Any:
    """namespace에서 단일 키 값을 조회 (없으면 default)."""
    data = load(namespace)
    return data.get(key, default)


def set(namespace: str, key: str, value: Any) -> None:  # noqa: A001 (의도적 shadowing)
    """namespace에 단일 키-값을 갱신 후 저장."""
    lock = _get_lock(namespace)
    with lock:
        # load는 자체적으로 lock을 잡으므로 캐시에서 직접 가져온다
        current = dict(_cache.get(namespace) or {})
    # 현재 캐시 없으면 디스크에서 로드
    if not current:
        current = load(namespace)
    current[key] = value
    save(namespace, current)


def get_all() -> Dict[str, Dict[str, Any]]:
    """모든 namespace의 영속 데이터를 dict로 반환.

    저장 디렉토리를 스캔해 발견된 모든 ``*.json`` 파일을 로드한다.
    캐시에만 존재하고 디스크에 없는 namespace도 포함된다.

    Returns:
        ``{namespace: {key: value, ...}, ...}`` 형태의 dict.
    """
    result: Dict[str, Dict[str, Any]] = {}

    # 디스크상의 모든 namespace 스캔
    try:
        for path in _BASE_DIR.glob("*.json"):
            namespace = path.stem
            try:
                result[namespace] = load(namespace)
            except Exception:
                # 개별 namespace 로드 실패는 건너뜀
                result[namespace] = {}
    except OSError:
        # 디렉토리 접근 실패 시 캐시 기반으로만 반환
        pass

    # 캐시에만 존재하는 namespace도 포함
    with _locks_guard:
        cached_namespaces = list(_cache.keys())
    for ns in cached_namespaces:
        if ns not in result:
            result[ns] = dict(_cache.get(ns) or {})

    return result


def set_log_raw_channel_id(channel_id: Optional[int]) -> None:
    """원시 로그 채널 ID를 영속 저장.

    Args:
        channel_id: 디스코드 채널 ID. ``None``인 경우도 그대로 저장한다.
    """
    set(_LOG_RAW_NS, _LOG_RAW_KEY, channel_id)


def get_log_raw_channel_id() -> Optional[int]:
    """저장된 원시 로그 채널 ID 반환 (없으면 None)."""
    value = get(_LOG_RAW_NS, _LOG_RAW_KEY, None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None