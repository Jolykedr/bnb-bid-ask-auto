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
    ladder_group_id: Optional[str] = None  # UUID grouping positions created together


@dataclass
class FeeSnapshotRecord:
    id: Optional[int]
    token_id: int
    chain_id: int
    protocol: str
    unclaimed_fees_usd: float
    position_value_usd: float
    smoothed_daily_apr: Optional[float]
    created_at: float              # unix timestamp


@dataclass
class ClaimedFeeRecord:
    id: Optional[int]
    token_id: int
    chain_id: int
    protocol: str            # v3, v3_uniswap, v4, v4_pancake
    fees_token0: float       # amount in token0 human-readable
    fees_token1: float       # amount in token1 human-readable
    fees_usd: float          # estimated USD value at collection time
    tx_hash: str
    collected_at: float      # unix timestamp


def _get_conn() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    # Wait up to 2s when another writer holds the lock instead of failing
    # immediately. Typical write completes in <50ms, so 2s is a 40x buffer
    # for normal contention (dashboard refresh racing against save_trade).
    # Persistent failures still surface as OperationalError.
    conn.execute("PRAGMA busy_timeout=2000")
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
        CREATE TABLE IF NOT EXISTS claimed_fees (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id    INTEGER NOT NULL,
            chain_id    INTEGER NOT NULL DEFAULT 56,
            protocol    TEXT NOT NULL DEFAULT 'v3',
            fees_token0 REAL DEFAULT 0,
            fees_token1 REAL DEFAULT 0,
            fees_usd    REAL DEFAULT 0,
            tx_hash     TEXT,
            collected_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fee_snapshots (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            token_id            INTEGER NOT NULL,
            chain_id            INTEGER NOT NULL DEFAULT 56,
            protocol            TEXT NOT NULL DEFAULT 'v3',
            unclaimed_fees_usd  REAL NOT NULL,
            position_value_usd  REAL NOT NULL,
            smoothed_daily_apr  REAL,
            created_at          REAL NOT NULL
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
    # Migration: add invested_usd column to open_positions
    try:
        conn.execute("ALTER TABLE open_positions ADD COLUMN invested_usd REAL DEFAULT 0")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: add ladder_group_id column to open_positions
    try:
        conn.execute("ALTER TABLE open_positions ADD COLUMN ladder_group_id TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Migration: add ladder_group_id column to trades
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN ladder_group_id TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    conn.commit()
    return conn


def save_trade(record: TradeRecord) -> int:
    """Insert a closed trade record. Returns the row id.

    Retry up to 3 times on `database is locked` (rare with busy_timeout=2s,
    but possible if AV/backup briefly holds the file). PnL history is
    user-visible and worth retrying for; other writers tolerate failure.
    Other OperationalErrors (constraint violations, etc.) propagate
    immediately so real bugs aren't masked.
    """
    last_err: Optional[Exception] = None
    for attempt in range(3):
        conn = _get_conn()
        try:
            cur = conn.execute(
                """INSERT INTO trades
                   (pair, chain_id, protocol, n_positions,
                    invested_usd, received_usd, pnl_usd, pnl_percent,
                    tx_hash, closed_at, ladder_group_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.pair, record.chain_id, record.protocol,
                 record.n_positions, record.invested_usd, record.received_usd,
                 record.pnl_usd, record.pnl_percent,
                 record.tx_hash, record.closed_at, record.ladder_group_id),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            last_err = e
            logger.warning(f"save_trade locked (attempt {attempt+1}/3): {e}")
            time.sleep(0.5 * (attempt + 1))
        finally:
            conn.close()
    # All 3 attempts exhausted — surface the lock failure to the caller
    # so the UI can warn the user that PnL history wasn't recorded.
    raise last_err  # type: ignore[misc]


def get_all_trades() -> List[TradeRecord]:
    """Return all trades ordered by closed_at descending."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, pair, chain_id, protocol, n_positions, "
            "invested_usd, received_usd, pnl_usd, pnl_percent, "
            "tx_hash, closed_at, ladder_group_id "
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
            "tx_hash, closed_at, ladder_group_id "
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


# ── Claimed Fees tracking ─────────────────────────────────

def save_claimed_fee(record: ClaimedFeeRecord) -> int:
    """Insert a claimed fee record. Returns the row id."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO claimed_fees
               (token_id, chain_id, protocol,
                fees_token0, fees_token1, fees_usd,
                tx_hash, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.token_id, record.chain_id, record.protocol,
             record.fees_token0, record.fees_token1, record.fees_usd,
             record.tx_hash, record.collected_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def save_claimed_fees_bulk(records: List[ClaimedFeeRecord]):
    """Insert multiple claimed fee records in one transaction."""
    if not records:
        return
    conn = _get_conn()
    try:
        conn.executemany(
            """INSERT INTO claimed_fees
               (token_id, chain_id, protocol,
                fees_token0, fees_token1, fees_usd,
                tx_hash, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(r.token_id, r.chain_id, r.protocol,
              r.fees_token0, r.fees_token1, r.fees_usd,
              r.tx_hash, r.collected_at) for r in records],
        )
        conn.commit()
    finally:
        conn.close()


def get_claimed_fees(token_id: int = None) -> List[ClaimedFeeRecord]:
    """Return claimed fee records, optionally filtered by token_id."""
    conn = _get_conn()
    try:
        if token_id is not None:
            rows = conn.execute(
                "SELECT id, token_id, chain_id, protocol, "
                "fees_token0, fees_token1, fees_usd, tx_hash, collected_at "
                "FROM claimed_fees WHERE token_id = ? ORDER BY collected_at DESC",
                (token_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, token_id, chain_id, protocol, "
                "fees_token0, fees_token1, fees_usd, tx_hash, collected_at "
                "FROM claimed_fees ORDER BY collected_at DESC"
            ).fetchall()
        return [ClaimedFeeRecord(*r) for r in rows]
    finally:
        conn.close()


def get_total_claimed_fees_usd() -> float:
    """Return total USD value of all claimed fees."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(fees_usd), 0) FROM claimed_fees"
        ).fetchone()
        return row[0]
    finally:
        conn.close()


def get_claimed_fees_usd_for_tokens(token_ids: list) -> float:
    """Return total claimed fees USD for specific token_ids."""
    if not token_ids:
        return 0.0
    conn = _get_conn()
    try:
        placeholders = ",".join("?" for _ in token_ids)
        row = conn.execute(
            f"SELECT COALESCE(SUM(fees_usd), 0) FROM claimed_fees "
            f"WHERE token_id IN ({placeholders})",
            token_ids,
        ).fetchone()
        return row[0]
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
                fee, tick_lower, tick_upper, liquidity, created_at,
                invested_usd, ladder_group_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                pos.get('invested_usd', 0),
                pos.get('ladder_group_id'),
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
            if not isinstance(pos, dict) or int(pos.get('liquidity', 0)) <= 0:
                continue
            pos_with_id = dict(pos, token_id=tid)
            conn.execute(
                """INSERT OR REPLACE INTO open_positions
                   (token_id, wallet, chain_id, protocol,
                    token0, token1, token0_symbol, token1_symbol,
                    token0_decimals, token1_decimals,
                    fee, tick_lower, tick_upper, liquidity, created_at,
                    invested_usd, ladder_group_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    pos.get('invested_usd', 0),
                    pos.get('ladder_group_id'),
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
                "fee, tick_lower, tick_upper, liquidity, created_at, "
                "invested_usd, ladder_group_id "
                "FROM open_positions WHERE wallet = ?",
                (wallet.lower(),)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT token_id, wallet, chain_id, protocol, "
                "token0, token1, token0_symbol, token1_symbol, "
                "token0_decimals, token1_decimals, "
                "fee, tick_lower, tick_upper, liquidity, created_at, "
                "invested_usd, ladder_group_id "
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
                'invested_usd': r[15] if len(r) > 15 else 0,
                'ladder_group_id': r[16] if len(r) > 16 else None,
            }
        return result
    finally:
        conn.close()


# ── Fee Snapshots (APR calculation) ──────────────────────────

def save_fee_snapshot(record: FeeSnapshotRecord) -> int:
    """Insert a fee snapshot. Returns the row id."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO fee_snapshots
               (token_id, chain_id, protocol,
                unclaimed_fees_usd, position_value_usd,
                smoothed_daily_apr, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (record.token_id, record.chain_id, record.protocol,
             record.unclaimed_fees_usd, record.position_value_usd,
             record.smoothed_daily_apr, record.created_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_latest_snapshot(token_id: int) -> Optional[FeeSnapshotRecord]:
    """Return the most recent fee snapshot for a token_id."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, token_id, chain_id, protocol, "
            "unclaimed_fees_usd, position_value_usd, "
            "smoothed_daily_apr, created_at "
            "FROM fee_snapshots WHERE token_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (token_id,)
        ).fetchone()
        return FeeSnapshotRecord(*row) if row else None
    finally:
        conn.close()


def get_latest_snapshots_bulk(token_ids: list) -> dict:
    """Return latest snapshot per token_id as {token_id: FeeSnapshotRecord}.

    Uses a single query for efficiency.
    """
    if not token_ids:
        return {}
    conn = _get_conn()
    try:
        placeholders = ",".join("?" for _ in token_ids)
        rows = conn.execute(
            f"SELECT id, token_id, chain_id, protocol, "
            f"unclaimed_fees_usd, position_value_usd, "
            f"smoothed_daily_apr, created_at "
            f"FROM fee_snapshots "
            f"WHERE token_id IN ({placeholders}) "
            f"ORDER BY token_id, created_at DESC",
            token_ids,
        ).fetchall()

        result = {}
        for r in rows:
            tid = r[1]
            if tid not in result:  # first row per token_id is latest (ORDER BY DESC)
                result[tid] = FeeSnapshotRecord(*r)
        return result
    finally:
        conn.close()


def prune_old_snapshots(retention_hours: int = 24):
    """Delete snapshots older than retention_hours, keeping the latest per token_id."""
    conn = _get_conn()
    try:
        cutoff = time.time() - retention_hours * 3600
        # Keep the latest snapshot per token_id regardless of age
        conn.execute(
            """DELETE FROM fee_snapshots
               WHERE created_at < ?
               AND id NOT IN (
                   SELECT id FROM (
                       SELECT id, token_id,
                              ROW_NUMBER() OVER (PARTITION BY token_id ORDER BY created_at DESC) AS rn
                       FROM fee_snapshots
                   ) WHERE rn = 1
               )""",
            (cutoff,),
        )
        conn.commit()
    except Exception:
        logger.warning("Fee snapshot pruning failed", exc_info=True)
    finally:
        conn.close()
