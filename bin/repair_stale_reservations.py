#!/usr/bin/env python
"""
One-shot repair for stale hostify_reservations snapshot rows.

Background:
  Before the two-stage normalization fix, _normalize_reservation dropped
  cancelled+payout=0 reservations BEFORE save_hostify_reservations got a chance
  to UPSERT the new state. This left the snapshot stuck on the previous
  `status="accepted"` payload forever, so the report continued to show late-
  cancelled bookings as if they were active.

What this does:
  1. Build an index from the freshest hostify_cache entries: confirmation_code → raw dict.
  2. Find snapshot rows whose status='accepted' but whose freshest cache
     counterpart is cancelled.
  3. Re-write those rows via save_hostify_reservations + the new
     normalize_reservations_for_snapshot — payload_json gets refreshed,
     last_seen_at moves forward, status flips to 'cancelled'.

After running, regenerate any open months (UI bulk regen, or wait for the
nightly hostify_sync) to drop the now-correctly-cancelled rows from
report_rows.

Idempotent: safe to re-run.
"""
from __future__ import annotations

import json
import os
import sys

# When run from /home/rentero/rentero, this path puts the project root on sys.path.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from report.db import get_connection, save_hostify_reservations
from report.loader import normalize_reservations_for_snapshot


def main() -> int:
    conn = get_connection()
    try:
        # 1. Build "freshest snapshot per code" from hostify_cache.
        idx: dict[str, dict] = {}
        cache_rows = conn.execute(
            "SELECT cache_key, data, fetched_at FROM hostify_cache "
            "ORDER BY fetched_at DESC"
        ).fetchall()
        for row in cache_rows:
            try:
                payload = json.loads(row["data"])
            except Exception as exc:
                print(f"[skip] cache_key={row['cache_key']} unreadable: {exc}")
                continue
            for r in payload:
                cc = str(
                    r.get("confirmation_code")
                    or r.get("channel_reservation_id")
                    or ""
                )
                if cc and cc not in idx:  # iterating desc → first hit is newest
                    idx[cc] = r

        print(f"Indexed {len(idx)} unique reservations from hostify_cache.")

        # 2. Find candidates: snapshot says accepted, fresh cache says cancelled.
        snapshot_rows = conn.execute(
            "SELECT confirmation_code, status, listing_nickname, check_in "
            "  FROM hostify_reservations "
            " WHERE status = 'accepted' AND check_in >= '2025-01-01'"
        ).fetchall()

        to_repair: list[dict] = []
        for s in snapshot_rows:
            cc = s["confirmation_code"]
            fresh = idx.get(cc)
            if not fresh:
                continue
            if (fresh.get("status") or "").lower() == "cancelled":
                to_repair.append(fresh)

        print(f"Stale 'accepted' rows whose fresh state is 'cancelled': {len(to_repair)}")
        if not to_repair:
            print("Nothing to repair.")
            return 0

        for r in to_repair[:20]:
            cc = r.get("confirmation_code") or r.get("channel_reservation_id") or ""
            listing = r.get("listing_nickname") or ""
            check_in = r.get("check_in") or r.get("checkIn") or ""
            print(
                f"  {cc:20} listing={listing:30} "
                f"check_in={check_in} payout={r.get('payout_price')}"
            )
        if len(to_repair) > 20:
            print(f"  ... and {len(to_repair) - 20} more")

        # 3. UPSERT through the new snapshot-level normalizer.
        normalized = normalize_reservations_for_snapshot(to_repair)
        save_hostify_reservations(conn, normalized)
        print(f"Snapshot updated: {len(normalized)} rows refreshed.")
        print(
            "Next step: run bulk regen of open months "
            "(or wait for nightly hostify_sync) to drop these from report_rows."
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
