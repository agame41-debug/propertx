"""Regenerate all OPEN (slug, year, month) report snapshots.

Used after a system-wide loader fix to flush new data into report_rows
and into payout_batch_bank_matches.  LOCKED months are skipped by the
engine itself.

Usage (on prod):
    PYTHONPATH=/home/rentero/rentero ./venv/bin/python bin/regen_all_open.py
"""
from __future__ import annotations

import logging
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("regen_all_open")
log.setLevel(logging.INFO)


def main() -> int:
    from report.config import get_all_properties, load_runtime_config
    from report.db import get_connection
    from report.engine import generate_report_in_process

    conn = get_connection()
    try:
        config = load_runtime_config(
            os.path.join(_PROJECT_ROOT, "config", "properties.json"),
            db_conn=conn,
        )
        active_slugs = {p["slug"] for p in get_all_properties(config) if p.get("active", True)}

        rows = conn.execute(
            "SELECT slug, year, month FROM report_month_state "
            "WHERE status = 'OPEN' "
            "ORDER BY year, month, slug"
        ).fetchall()
        targets = [
            (r["slug"], r["year"], r["month"])
            for r in rows
            if r["slug"] in active_slugs
        ]
        log.info("Targets: %d (slug, year, month) tuples", len(targets))

        ok = skipped = failed = 0
        for slug, year, month in targets:
            try:
                result = generate_report_in_process(conn, slug, year, month, config)
                if result.get("skipped"):
                    skipped += 1
                else:
                    ok += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log.exception("FAIL %s %d-%02d: %s", slug, year, month, exc)
            if (ok + skipped + failed) % 50 == 0:
                log.info("Progress: ok=%d skipped=%d failed=%d / %d",
                         ok, skipped, failed, len(targets))
        log.info("Done. ok=%d skipped=%d failed=%d", ok, skipped, failed)
        return 1 if failed else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
