"""
utils/openrouter_client.py
모든 LLM 호출의 단일 진입점.
- OpenRouter API 경유 → usage.cost 자동 수집
- 모델 티어링 (런타임 변경 가능)
- in-memory LRU 응답 캐싱 10분
- 모델 호출 실패 시 자동 폴백
"""

import hashlib
import logging
import os
import time
from typing import Optional

import aiohttp
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
CREDITS_URL = "https://openrouter.ai/api/v1/credits"

# ── 모델 티어링 (런타임 변경 가능) ──────────────────────────────────
# /model_set 커맨드로 변경되며, utils/model_config.py가 영속화를 담당.
MODEL_TIERS: dict[str, str] = {
    "router":   "openai/gpt-5.4-nano",          # 라우팅 판단
    "light":    "openai/gpt-5.4-nano",          # 단순 Q&A, 스케줄
    "standard": "anthropic/claude-opus-4.7",    # 기획, R&D
    "premium":  "anthropic/claude-opus-4.7",    # 해쵸 종합
    "research": "perplexity/sonar-pro",         # 분쵸 리서치
    "vision":   "openai/gpt-4o",                # 디쵸 디자인
}

# 모델 호출 실패 시 자동 폴백 체인
FALLBACK_CHAIN: dict[str, list[str]] = {
    "router":   ["openai/gpt-5-nano", "ggoogle/gemini-3.1-flash-lite"],
    "light":    ["openai/gpt-5-nano", "google/gemini-3.1-flash-lite"],
    "standard": ["anthropic/claude-3.5-sonnet", "anthropic/claude-3-opus"],
    "premium":  ["anthropic/claude-3.5-sonnet", "anthropic/claude-3-opus"],
    "research": ["perplexity/sonar"],
    "vision":   ["openai/gpt-4o-mini"],
}

# ── 페르소나별 기본 티어 매핑 ───────────────────────────────────────
AGENT_TIER: dict[str, str] = {
    "haecho":  "premium",   # 해쵸 종합 브리핑 = opus 4.7
    "gihyo":   "standard",  # 기쵸 = opus 4.7
    "bunchyo": "research",  # 분쵸 = perplexity sonar-pro
    "mochyo":  "light",     # 모쵸 = gpt-5.4-nano
    "sochyo":  "light",     # 스쵸 = gpt-5.4-nano
    "inchyo":  "light",     # 인쵸 = gpt-5.4-nano
    "gaechyo": "standard",  # 개쵸 = opus 4.7
    "dichyo":  "vision",    # 디쵸 = gpt-4o
    "router":  "router",    # 라우터 = gpt-5.4-nano
}


# ── 부팅 시 영속화 설정 로드 ────────────────────────────────────────
def _load_persisted_config():
    """utils/model_config.py가 저장해둔 오버라이드를 적용."""
    try:
        from utils.model_config import load_overrides
        overrides = load_overrides()
        for tier, model in overrides.get("tiers", {}).items():
            if tier in MODEL_TIERS:
                MODEL_TIERS[tier] = model
        for agent, tier in overrides.get("agents", {}).items():
            if agent in AGENT_TIER:
                AGENT_TIER[agent] = tier
        if overrides:
            log.info(f"모델 설정 오버라이드 적용: {len(overrides.get('tiers', {}))} tiers, "
                     f"{len(overrides.get('agents', {}))} agents")
    except Exception as e:
        log.debug(f"영속화 설정 로드 스킵: {e}")


_load_persisted_config()


# ── 응답 캐시 (TTL 600초) ───────────────────────────────────────────
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 600


def _cache_key(model: str, messages: list) -> str:
    raw = f"{model}:{str(messages)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if entry and time.time() - entry[0] < _CACHE_TTL:
        return entry[1]
    if entry:
        _cache.pop(key, None)
    return None


def _cache_set(key: str, value: dict) -> None:
    if len(_cache) > 200:
        oldest = min(_cache.items(), key=lambda kv: kv[1][0])
        _cache.pop(oldest[0], None)
    _cache[key] = (time.time(), value)


# ── 런타임 모델 변경 API ────────────────────────────────────────────
def set_tier_model(tier: str, model: str, *, persist: bool = True) -> None:
    """특정 티어의 모델을 변경. persist=True면 파일로 영속화."""
    if tier not in MODEL_TIERS:
        raise ValueError(f"알 수 없는 tier: {tier}")
    MODEL_TIERS[tier] = model
    if persist:
        from utils.model_config import save_tier_override
        save_tier_override(tier, model)
    log.info(f"티어 모델 변경: {tier} → {model}")


def set_agent_tier(agent: str, tier: str, *, persist: bool = True) -> None:
    """에이전트의 기본 티어를 변경."""
    if agent not in AGENT_TIER:
        raise ValueError(f"알 수 없는 agent: {agent}")
    if tier not in MODEL_TIERS:
        raise ValueError(f"알 수 없는 tier: {tier}")
    AGENT_TIER[agent] = tier
    if persist:
        from utils.model_config import save_agent_override
        save_agent_override(agent, tier)
    log.info(f"에이전트 티어 변경: {agent} → {tier}")


def get_current_config() -> dict:
    """현재 티어링·에이전트 매핑 스냅샷."""
    return {
        "tiers": dict(MODEL_TIERS),
        "agents": dict(AGENT_TIER),
    }


# ── 메인 호출 함수 ──────────────────────────────────────────────────
async def chat(
    messages: list[dict],
    *,
    agent: str = "haecho",
    tier: Optional[str] = None,
    model_override: Optional[str] = None,
    max_tokens: int = 800,
    temperature: float = 0.7,
    use_cache: bool = False,
    response_format: Optional[dict] = None,
) -> dict:
    """
    OpenRouter 호출.
    우선순위: model_override > tier > AGENT_TIER[agent]
    반환: {"content": str, "usage": {...}, "cost": float, "model": str}
    """
    from utils.cost_tracker import record_usage
    from utils.pipeline_logger import step

    # 모델 결정
    if model_override:
        primary_model = model_override
        tier_name = "override"
    else:
        tier_name = tier or AGENT_TIER.get(agent, "standard")
        primary_model = MODEL_TIERS[tier_name]

    # 폴백 체인 구성
    models_to_try = [primary_model]
    if tier_name in FALLBACK_CHAIN:
        models_to_try.extend(FALLBACK_CHAIN[tier_name])

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY 미설정")

    # 캐시 (primary 모델 기준)
    key = _cache_key(primary_model, messages)
    if use_cache:
        cached = _cache_get(key)
        if cached:
            step(f"OpenRouter [{agent}] CACHE HIT", "ok", f"{primary_model}")
            return cached

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://chos-management.bot",
        "X-Title": "Cho's Management Bot",
    }

    last_error = None
    for attempt_idx, model in enumerate(models_to_try):
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "usage": {"include": True},
        }
        if response_format:
            payload["response_format"] = response_format

        t = time.monotonic()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    OPENROUTER_URL, headers=headers, json=payload, timeout=90
                ) as resp:
                    data = await resp.json()
                    ms = int((time.monotonic() - t) * 1000)

                    if resp.status != 200:
                        err_msg = str(data)[:120]
                        last_error = f"HTTP {resp.status}: {err_msg}"
                        tag = "FALLBACK" if attempt_idx > 0 else ""
                        step(f"OpenRouter [{agent}]{tag}", "fail",
                             f"{model} | {err_msg}", "E004", ms)
                        # 폴백 시도
                        if attempt_idx < len(models_to_try) - 1:
                            log.warning(f"모델 {model} 실패 → 폴백 시도")
                            continue
                        raise RuntimeError(f"모든 모델 실패: {last_error}")

                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    cost = float(usage.get("cost", 0))

                    result = {
                        "content": content,
                        "usage": usage,
                        "cost": cost,
                        "model": model,
                    }
                    tag = " (FALLBACK)" if attempt_idx > 0 else ""
                    step(
                        f"OpenRouter [{agent}]{tag}", "ok",
                        f"{model} | {usage.get('total_tokens', 0)}tok | ${cost:.5f}",
                        duration_ms=ms,
                    )
                    await record_usage(agent, model, usage, cost)
                    if use_cache:
                        _cache_set(key, result)
                    return result

        except aiohttp.ClientError as e:
            ms = int((time.monotonic() - t) * 1000)
            last_error = str(e)[:80]
            step(f"OpenRouter [{agent}]", "fail",
                 f"{model} | {last_error}", "E004", ms)
            if attempt_idx < len(models_to_try) - 1:
                continue
            raise

    raise RuntimeError(f"OpenRouter 호출 최종 실패: {last_error}")


async def get_remaining_credits() -> dict:
    """잔여 크레딧 조회."""
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return {"total": 0, "usage": 0, "remaining": 0, "usage_ratio": 0}
    headers = {"Authorization": f"Bearer {key}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(CREDITS_URL, headers=headers) as resp:
            data = (await resp.json()).get("data", {})
            total = float(data.get("total_credits", 0))
            used = float(data.get("total_usage", 0))
            return {
                "total": total,
                "usage": used,
                "remaining": total - used,
                "usage_ratio": used / total if total else 0,
            }


async def list_available_models() -> list[dict]:
    """
    OpenRouter에서 사용 가능한 모델 목록 조회.
    /model_list 커맨드에서 자동완성 지원용.
    """
    key = os.getenv("OPENROUTER_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://openrouter.ai/api/v1/models", headers=headers
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("data", [])