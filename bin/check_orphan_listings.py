#!/usr/bin/env python3
"""Rentero admin CLI — list Hostify listing nicknames without an alias.

An "orphan" listing is a listing_nickname present in hostify_reservations
that has no active alias in report_object_aliases (channel='hostify',
alias_type='listing_nickname'). Their reservations are silently dropped
during regen — they exist in the DB but never appear in report_rows.

Usage:
    bin/check_orphan_listings.py                 — list orphans (no DB writes)
    bin/check_orphan_listings.py --record        — also persist into
                                                   hostify_orphan_listings
                                                   (same as the daily sync)
"""

from __future__ import annotations

import argparse
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--record",
        action="store_true",
        help="Persist orphans into hostify_orphan_listings and report delta.",
    )
    args = parser.parse_args(argv)

    from report.db import (
        find_orphan_listing_nicknames,
        get_connection,
        record_orphan_listings,
    )

    conn = get_connection()
    try:
        if args.record:
            delta = record_orphan_listings(conn)
            orphans = find_orphan_listing_nicknames(conn)
            print(
                f"Recorded {delta['current_count']} orphans "
                f"({len(delta['newly_detected'])} new, "
                f"{len(delta['resolved'])} resolved)."
            )
        else:
            orphans = find_orphan_listing_nicknames(conn)

        if not orphans:
            print("No orphan listings — every Hostify nickname is mapped.")
            return 0

        print(f"{len(orphans)} orphan listing nicknames:\n")
        header = (
            f"  {'NICKNAME':<40} {'SRC':<13} {'#':>4}  "
            f"{'CHECK-IN RANGE':<25} HOSTIFY_ID"
        )
        print(header)
        print("  " + "-" * (len(header) - 2))
        for o in orphans:
            nick = o["listing_nickname"]
            src = o["sources"] or "—"
            count = o["reservation_count"]
            ci_range = f"{o['first_check_in'] or '—'} .. {o['last_check_in'] or '—'}"
            lid = o["example_listing_id"] or "—"
            print(f"  {nick:<40} {src:<13} {count:>4}  {ci_range:<25} {lid}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
