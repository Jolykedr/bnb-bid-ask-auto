"""
Daily APR calculation via fee snapshots + EWMA smoothing.

On every position refresh the caller saves a snapshot per position.
APR is computed from the fee delta between consecutive snapshots,
then smoothed with Exponential Weighted Moving Average (alpha=0.3).
"""

import time
import logging

from src.storage.pnl_store import (
    FeeSnapshotRecord,
    save_fee_snapshot,
    get_latest_snapshots_bulk,
    prune_old_snapshots,
)

logger = logging.getLogger(__name__)

# EWMA smoothing factor: 0.3 = 30% weight on latest observation
EWMA_ALPHA = 0.3

# Minimum seconds between snapshots to avoid division-by-zero / noise
MIN_SNAPSHOT_INTERVAL = 30

# Prune snapshots older than this
SNAPSHOT_RETENTION_HOURS = 24


def calc_position_apr(
    token_id: int,
    chain_id: int,
    protocol: str,
    unclaimed_fees_usd: float,
    position_value_usd: float,
    prev_snapshot: FeeSnapshotRecord | None,
) -> float | None:
    """
    Calculate daily APR for a single position and save a new snapshot.

    1. fee_delta = unclaimed_fees_usd - prev.unclaimed_fees_usd
    2. seconds_elapsed = now - prev.created_at
    3. raw_daily_apr = (fee_delta / seconds_elapsed) * 86400 / position_value_usd * 100
    4. smoothed = EWMA_ALPHA * raw + (1 - EWMA_ALPHA) * prev.smoothed_daily_apr
    5. Save FeeSnapshot, return smoothed

    Returns smoothed daily APR (%) or None if not enough data.
    """
    now = time.time()
    daily_apr: float | None = None

    if prev_snapshot and position_value_usd > 0:
        dt = now - prev_snapshot.created_at
        if dt >= MIN_SNAPSHOT_INTERVAL:
            fee_delta = unclaimed_fees_usd - prev_snapshot.unclaimed_fees_usd
            # fee_delta can be negative if fees were collected between snapshots
            if fee_delta >= 0:
                fee_rate_per_sec = fee_delta / dt
                raw_daily_apr = (fee_rate_per_sec * 86400 / position_value_usd) * 100

                # EWMA smoothing
                if prev_snapshot.smoothed_daily_apr is not None:
                    daily_apr = EWMA_ALPHA * raw_daily_apr + (1 - EWMA_ALPHA) * prev_snapshot.smoothed_daily_apr
                else:
                    daily_apr = raw_daily_apr
            else:
                # Fees were collected — carry forward previous smoothed value
                daily_apr = prev_snapshot.smoothed_daily_apr
        else:
            # Too soon — carry forward previous value, don't save new snapshot
            return prev_snapshot.smoothed_daily_apr
    elif prev_snapshot and prev_snapshot.smoothed_daily_apr is not None:
        # Value is 0 but we had a previous APR — carry forward
        daily_apr = prev_snapshot.smoothed_daily_apr

    rounded_apr = round(daily_apr, 4) if daily_apr is not None else None

    # Save new snapshot
    snap = FeeSnapshotRecord(
        id=None,
        token_id=token_id,
        chain_id=chain_id,
        protocol=protocol,
        unclaimed_fees_usd=unclaimed_fees_usd,
        position_value_usd=position_value_usd,
        smoothed_daily_apr=rounded_apr,
        created_at=now,
    )
    save_fee_snapshot(snap)

    return rounded_apr


def calc_aggregate_apr(apr_map: dict[int, float | None], value_map: dict[int, float]) -> float | None:
    """
    Compute value-weighted average daily APR across positions.

    apr_map: {token_id: daily_apr_or_None}
    value_map: {token_id: position_value_usd}
    """
    total_weight = 0.0
    weighted_sum = 0.0

    for tid, apr in apr_map.items():
        val = value_map.get(tid, 0)
        if apr is not None and val > 0:
            weighted_sum += apr * val
            total_weight += val

    if total_weight <= 0:
        return None

    return round(weighted_sum / total_weight, 4)
