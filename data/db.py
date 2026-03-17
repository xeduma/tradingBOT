"""
Couche base de données — TimescaleDB (PostgreSQL).
Stockage des ticks, trades, métriques et positions.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from utils.logger import setup_logger

logger = setup_logger("database")

# ── Schéma SQL (exécuté au démarrage si les tables n'existent pas) ─────────────
SCHEMA_SQL = """
-- Extension TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Table des bougies OHLCV
CREATE TABLE IF NOT EXISTS candles (
    time        TIMESTAMPTZ NOT NULL,
    symbol      TEXT        NOT NULL,
    exchange    TEXT        NOT NULL,
    timeframe   TEXT        NOT NULL,
    open        DOUBLE PRECISION,
    high        DOUBLE PRECISION,
    low         DOUBLE PRECISION,
    close       DOUBLE PRECISION,
    volume      DOUBLE PRECISION,
    PRIMARY KEY (time, symbol, timeframe)
);
SELECT create_hypertable('candles', 'time', if_not_exists => TRUE);

-- Table des trades exécutés
CREATE TABLE IF NOT EXISTS trades (
    id              TEXT        PRIMARY KEY,
    symbol          TEXT        NOT NULL,
    exchange        TEXT,
    side            TEXT        NOT NULL,
    quantity        DOUBLE PRECISION,
    entry_price     DOUBLE PRECISION,
    close_price     DOUBLE PRECISION,
    leverage        INTEGER     DEFAULT 1,
    stop_loss       DOUBLE PRECISION,
    tp1             DOUBLE PRECISION,
    tp2             DOUBLE PRECISION,
    pnl             DOUBLE PRECISION,
    close_reason    TEXT,
    latency_ms      DOUBLE PRECISION,
    mistral_score   INTEGER,
    open_at         TIMESTAMPTZ DEFAULT NOW(),
    closed_at       TIMESTAMPTZ,
    status          TEXT        DEFAULT 'open'
);

-- Table des métriques journalières
CREATE TABLE IF NOT EXISTS daily_metrics (
    date            DATE        PRIMARY KEY,
    pnl             DOUBLE PRECISION,
    trades_total    INTEGER,
    trades_win      INTEGER,
    trades_loss     INTEGER,
    win_rate        DOUBLE PRECISION,
    sharpe          DOUBLE PRECISION,
    max_drawdown    DOUBLE PRECISION,
    capital_end     DOUBLE PRECISION
);

-- Index optimisés
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol, open_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades (status);
"""


class Database:
    """
    Interface asynchrone PostgreSQL / TimescaleDB via asyncpg.
    Pool de connexions pour la haute concurrence.
    """

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        try:
            self._pool = await asyncpg.create_pool(
                self.db_url,
                min_size=2,
                max_size=10,
                command_timeout=10,
            )
            await self._initialize_schema()
            logger.info("Base de données connectée (TimescaleDB).")
        except Exception as e:
            logger.error(f"Impossible de se connecter à la DB : {e}")
            logger.warning("Continuité sans persistance (mode dégradé).")

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()

    async def _initialize_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)

    # ─── Trades ───────────────────────────────────────────────────────────────

    async def save_trade(self, order: dict) -> None:
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO trades
                    (id, symbol, side, quantity, entry_price, leverage,
                     stop_loss, tp1, tp2, latency_ms, status)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'open')
                ON CONFLICT (id) DO NOTHING
                """,
                order.get("id", ""),
                order.get("symbol", ""),
                order.get("side", ""),
                float(order.get("amount", 0)),
                float(order.get("average", 0)),
                int(order.get("leverage", 1)),
                float(order.get("stop_loss", 0)),
                float(order.get("tp1", 0)),
                float(order.get("tp2", 0)),
                float(order.get("latency_ms", 0)),
            )

    async def update_trade(
        self, order: dict, close_reason: str, pnl: float
    ) -> None:
        if not self._pool:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE trades
                SET close_price = $1,
                    pnl         = $2,
                    close_reason= $3,
                    closed_at   = NOW(),
                    status      = 'closed'
                WHERE id = $4
                """,
                float(order.get("average", 0)),
                pnl,
                close_reason,
                order.get("id", ""),
            )

    # ─── Bougies ──────────────────────────────────────────────────────────────

    async def save_candles(
        self, symbol: str, exchange: str, timeframe: str, candles: List[dict]
    ) -> None:
        if not self._pool or not candles:
            return
        rows = [
            (
                datetime.fromtimestamp(c["timestamp"] / 1000, tz=timezone.utc),
                symbol, exchange, timeframe,
                c["open"], c["high"], c["low"], c["close"], c["volume"],
            )
            for c in candles
        ]
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO candles (time, symbol, exchange, timeframe, open, high, low, close, volume)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT DO NOTHING
                """,
                rows,
            )

    # ─── Statistiques ─────────────────────────────────────────────────────────

    async def get_daily_stats(self, days: int = 30) -> List[dict]:
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    DATE(open_at)          AS date,
                    SUM(pnl)               AS pnl,
                    COUNT(*)               AS trades_total,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS trades_win
                FROM trades
                WHERE status = 'closed'
                  AND open_at >= NOW() - INTERVAL '$1 days'
                GROUP BY DATE(open_at)
                ORDER BY date DESC
                """,
                days,
            )
            return [dict(r) for r in rows]

    async def get_open_trades(self) -> List[dict]:
        if not self._pool:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM trades WHERE status = 'open'")
            return [dict(r) for r in rows]
