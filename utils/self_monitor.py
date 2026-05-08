"""
utils/self_monitor.py
봇 자체의 런타임 에러를 감지하고 개쵸(R&D 채널)에 자동 보고.

기능:
1. 에러 카운터 누적
2. 임계치 초과 시 R&D 채널 알림
3. 매 시간 에러 요약 리포트 (있을 때만)
"""

import logging
import time
from collections import defaultdict, deque
from datetime import datetime

log = logging.getLogger(__name__)

# 최근 1시간 에러 이력 (최대 100건)
_errors: deque = deque(maxlen=100)
_error_counts: dict[str, int] = defaultdict(int)
_alerted_thresholds: set = set()

# 알림 임계치
ERROR_THRESHOLD = 5    # 같은 에러가 5번 이상 발생 시 R&D 채널 알림


def record_error(category: str, message: str, traceback_str: str = "") -> None:
    """에러 발생 기록."""
    _errors.append({
        "time": time.time(),
        "category": category,
        "message": message[:500],
        "traceback": traceback_str[:1500],
    })
    _error_counts[category] += 1


def get_recent_errors(minutes: int = 60) -> list[dict]:
    """최근 N분 내 에러 리스트."""
    cutoff = time.time() - minutes * 60
    return [e for e in _errors if e["time"] >= cutoff]


def get_error_summary() -> dict:
    """에러 카테고리별 집계."""
    return dict(_error_counts)


async def check_error_thresholds(bot) -> None:
    """임계치 초과 에러 발견 시 R&D 채널 알림."""
    from modules.rnd import post_to_rnd_channel

    for category, count in _error_counts.items():
        key = f"{category}:{count // ERROR_THRESHOLD}"
        if count >= ERROR_THRESHOLD and key not in _alerted_thresholds:
            _alerted_thresholds.add(key)

            recent = [e for e in _errors if e["category"] == category][-3:]
            samples = "\n".join(
                f"• `{datetime.fromtimestamp(e['time']):%H:%M:%S}` {e['message'][:100]}"
                for e in recent
            )

            await post_to_rnd_channel(
                bot,
                category="issue",
                title=f"에러 임계치 초과: {category} ({count}회)",
                content=(
                    f"카테고리 `{category}`의 에러가 **{count}회** 발생했습니다.\n\n"
                    f"**최근 샘플**:\n{samples}\n\n"
                    f"`/rnd_diagnose 이슈_설명`으로 진단 요청 가능"
                ),
                author="자동 감지",
            )
            log.warning(f"에러 임계치 알림 발송: {category} × {count}")


def reset_counters() -> None:
    """매일 자정 카운터 초기화."""
    _error_counts.clear()
    _alerted_thresholds.clear()