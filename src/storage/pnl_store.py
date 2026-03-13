"""
PnL Store — SQLite-based storage for closed trade records.

Tracks realized PnL, win/loss stats, and cumulative performance
for the Dashboard tab.
"""

import sqlite3
import os
import time
import logging
from typing import List, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.expanduser("~"), ".bnb_ladder")
DB_PATH = os.path.join(DB_DIR, "pnl.db")


@dataclass
class TradeRecord:
    id: Optional[int]
    pair: str                # e.g. "USDT/WBNB"
    chain_id: int
    protocol: str            # v3, v3_uniswap, v4, v4_pancake
    n_positions: int
    invested_usd: float
    received_usd: float      # value at close time
    pnl_usd: float
    pnl_percent: float
    tx_hash: str
    closed_at: float         # unix timestamp


def _get_conn() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pair        TEXT NOT NULL,
            chain_id    INTEGER NOT NULL DEFAULT 56,
            protocol    TEXT NOT NULL DEFAULT 'v3',
            n_positions INTEGER NOT NULL,
            invested_usd REAL NOT NULL,
            received_usd REAL NOT NULL,
            pnl_usd     REAL NOT NULL,
            pnl_percent  REAL NOT NULL,
            tx_hash     TEXT,
            closed_at   REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS open_positions (
            token_id    INTEGER PRIMARY KEY,
            wallet      TEXT NOT NULL,
            chain_id    INTEGER NOT NULL DEFAULT 56,
            protocol    TEXT NOT NULL DEFAULT 'v3',
            token0      TEXT NOT NULL,
            token1      TEXT NOT NULL,
            token0_symbol TEXT,
            token1_symbol TEXT,
            token0_decimals INTEGER DEFAULT 18,
            token1_decimals INTEGER DEFAULT 18,
            fee         INTEGER DEFAULT 0,
            tick_lower  INTEGER DEFAULT 0,
            tick_upper  INTEGER DEFAULT 0,
            liquidity   TEXT DEFAULT '0',
            created_at  REAL NOT NULL
        )
    """)
    conn.commit()
    return conn


def save_trade(record: TradeRecord) -> int:
    """Insert a closed trade record. Returns the row id."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO trades
               (pair, chain_id, protocol, n_positions,
                invested_usd, received_usd, pnl_usd, pnl_percent,
                tx_hash, closed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.pair, record.chain_id, record.protocol,
             record.n_positions, record.invested_usd, record.received_usd,
             record.pnl_usd, record.pnl_percent,
             record.tx_hash, record.closed_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_all_trades() -> List[TradeRecord]:
    """Return all trades ordered by closed_at descending."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, pair, chain_id, protocol, n_positions, "
            "invested_usd, received_usd, pnl_usd, pnl_percent, "
            "tx_hash, closed_at "
            "FROM trades ORDER BY closed_at DESC"
        ).fetchall()
        return [TradeRecord(*r) for r in rows]
    finally:
        conn.close()


def get_recent_trades(limit: int = 5) -> List[TradeRecord]:
    """Return the N most recent trades."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, pair, chain_id, protocol, n_positions, "
            "invested_usd, received_usd, pnl_usd, pnl_percent, "
            "tx_hash, closed_at "
            "FROM trades ORDER BY closed_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [TradeRecord(*r) for r in rows]
    finally:
        conn.close()


def get_dashboard_stats() -> dict:
    """Aggregate stats for the dashboard."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*), "
            "COALESCE(SUM(pnl_usd), 0), "
            "COALESCE(SUM(invested_usd), 0), "
            "COALESCE(SUM(received_usd), 0), "
            "COALESCE(SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END), 0) "
            "FROM trades"
        ).fetchone()

        total_trades, total_pnl, total_invested, total_received, wins, losses = row
        win_rate = round(wins / total_trades * 100, 1) if total_trades > 0 else 0.0

        return {
            'total_trades': total_trades,
            'total_realized_pnl': total_pnl,
            'total_invested': total_invested,
            'total_received': total_received,
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
        }
    finally:
        conn.close()


def get_cumulative_pnl() -> List[dict]:
    """Return cumulative PnL time series [{date, pnl}, ...]."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT closed_at, pnl_usd FROM trades ORDER BY closed_at ASC"
        ).fetchall()

        if not rows:
            return []

        cumulative = 0.0
        points = []
        for ts, pnl in rows:
            cumulative += pnl
            points.append({
                'date': ts,
                'pnl': round(cumulative, 2),
            })
        return points
    finally:
        conn.close()


def delete_trade(trade_id: int):
    """Delete a trade record by id."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
        conn.commit()
    finally:
        conn.close()


# ── Open Positions persistence ────────────────────────────

def save_open_position(wallet: str, pos: dict):
    """Upsert an open position. pos must contain token_id + metadata."""
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO open_positions
               (token_id, wallet, chain_id, protocol,
                token0, token1, token0_symbol, token1_symbol,
                token0_decimals, token1_decimals,
                fee, tick_lower, tick_upper, liquidity, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pos.get('token_id'),
                wallet.lower(),
                pos.get('chain_id', 56),
                pos.get('protocol', 'v3'),
                pos.get('token0', ''),
                pos.get('token1', ''),
                pos.get('token0_symbol', ''),
                pos.get('token1_symbol', ''),
                pos.get('token0_decimals', 18),
                pos.get('token1_decimals', 18),
                pos.get('fee', 0),
                pos.get('tick_lower', 0),
                pos.get('tick_upper', 0),
                str(pos.get('liquidity', 0)),
                time.time(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def save_open_positions_bulk(wallet: str, positions: dict):
    """Bulk upsert open positions. positions = {token_id: pos_dict, ...}."""
    conn = _get_conn()
    try:
        for tid, pos in positions.items():
            if not isinstance(pos, dict) or pos.get('liquidity', 0) <= 0:
                continue
            pos_with_id = dict(pos, token_id=tid)
            conn.execute(
                """INSERT OR REPLACE INTO open_positions
                   (token_id, wallet, chain_id, protocol,
                    token0, token1, token0_symbol, token1_symbol,
                    token0_decimals, token1_decimals,
                    fee, tick_lower, tick_upper, liquidity, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tid,
                    wallet.lower(),
                    pos.get('chain_id', 56),
                    pos.get('protocol', 'v3'),
                    pos.get('token0', ''),
                    pos.get('token1', ''),
                    pos.get('token0_symbol', ''),
                    pos.get('token1_symbol', ''),
                    pos.get('token0_decimals', 18),
                    pos.get('token1_decimals', 18),
                    pos.get('fee', 0),
                    pos.get('tick_lower', 0),
                    pos.get('tick_upper', 0),
                    str(pos.get('liquidity', 0)),
                    time.time(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def remove_open_positions(token_ids: list):
    """Remove positions by token IDs (they were closed)."""
    if not token_ids:
        return
    conn = _get_conn()
    try:
        placeholders = ",".join("?" for _ in token_ids)
        conn.execute(
            f"DELETE FROM open_positions WHERE token_id IN ({placeholders})",
            token_ids,
        )
        conn.commit()
    finally:
        conn.close()


def get_open_positions(wallet: str = None) -> dict:
    """Return open positions as {token_id: pos_dict}.

    If wallet is specified, filter by wallet address.
    """
    conn = _get_conn()
    try:
        if wallet:
            rows = conn.execute(
                "SELECT token_id, wallet, chain_id, protocol, "
                "token0, token1, token0_symbol, token1_symbol, "
                "token0_decimals, token1_decimals, "
                "fee, tick_lower, tick_upper, liquidity, created_at "
                "FROM open_positions WHERE wallet = ?",
                (wallet.lower(),)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT token_id, wallet, chain_id, protocol, "
                "token0, token1, token0_symbol, token1_symbol, "
                "token0_decimals, token1_decimals, "
                "fee, tick_lower, tick_upper, liquidity, created_at "
                "FROM open_positions"
            ).fetchall()

        result = {}
        for r in rows:
            token_id = r[0]
            result[token_id] = {
                'token_id': token_id,
                'wallet': r[1],
                'chain_id': r[2],
                'protocol': r[3],
                'token0': r[4],
                'token1': r[5],
                'token0_symbol': r[6],
                'token1_symbol': r[7],
                'token0_decimals': r[8],
                'token1_decimals': r[9],
                'fee': r[10],
                'tick_lower': r[11],
                'tick_upper': r[12],
                'liquidity': int(r[13]) if r[13] else 0,
                'created_at': r[14],
            }
        return result
    finally:
        conn.close()
