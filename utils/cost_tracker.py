"""
utils/cost_tracker.py
OpenRouter usage.cost를 SQLite에 누적 저장.
Railway Volume(/data) 사용.
"""

import aiosqlite
import logging
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)
DB_PATH = Path("/data/cost_tracker.db") if Path("/data").exists() else Path("./data/cost_tracker.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


async def _init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                agent TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                cost REAL DEFAULT 0
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ts ON usage_log(ts)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_agent ON usage_log(agent)")
        await db.commit()


async def record_usage(agent: str, model: str, usage: dict, cost: float) -> None:
    await _init()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO usage_log(ts, agent, model, prompt_tokens, completion_tokens, total_tokens, cost) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.utcnow().isoformat(),
                agent, model,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("total_tokens", 0),
                cost,
            ),
        )
        await db.commit()


async def get_monthly_total(year: int = None, month: int = None) -> float:
    await _init()
    now = datetime.utcnow()
    year = year or now.year
    month = month or now.month
    start = datetime(year, month, 1).isoformat()
    end = (datetime(year + (month // 12), (month % 12) + 1, 1)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(cost), 0) FROM usage_log WHERE ts >= ? AND ts < ?",
            (start, end),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0.0


async def get_by_agent(days: int = 30) -> dict[str, float]:
    await _init()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT agent, SUM(cost) FROM usage_log WHERE ts >= ? GROUP BY agent",
            (since,),
        ) as cur:
            return {row[0]: row[1] for row in await cur.fetchall()}


async def get_by_model(days: int = 30) -> dict[str, float]:
    await _init()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT model, SUM(cost) FROM usage_log WHERE ts >= ? GROUP BY model",
            (since,),
        ) as cur:
            return {row[0]: row[1] for row in await cur.fetchall()}


async def get_daily_series(days: int = 30) -> list[dict]:
    await _init()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT DATE(ts) as d, SUM(cost) as c FROM usage_log "
            "WHERE ts >= ? GROUP BY d ORDER BY d",
            (since,),
        ) as cur:
            return [{"date": row[0], "cost": row[1]} for row in await cur.fetchall()]


async def project_next_month() -> float:
    """
    최근 14일 평균 × 30 으로 다음 달 예상.
    """
    daily = await get_daily_series(days=14)
    if not daily:
        return 0.0
    avg = sum(d["cost"] for d in daily) / len(daily)
    return avg * 30