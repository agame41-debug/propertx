"""One-shot reimport of all active Hlavní kniha source files.

Use case: after fixing the abs() bug in load_hlavni_kniha_from_bytes
(report/accounting.py:658-660), the previously-loaded
accounting_entries.castka values lost the sign on every korekce. This
script re-runs the loader on all active accounting source files (whose
binary content is stored in source_files.content) so castka becomes
signed again.

save_accounting_entries does DELETE + INSERT per source_file_id, so
repeated runs are idempotent. Read-only on source_files.

Usage (on prod, from /home/rentero/rentero):
    ./venv/bin/python bin/reimport_accounting.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sys


def main(dry_run: bool) -> int:
    from report.db import (
        get_active_source_files,
        get_connection,
        get_stredisko_map,
        save_accounting_entries,
    )
    from report.accounting import load_hlavni_kniha_from_bytes

    db_path = "/home/rentero/rentero/cache/rentero.db"
    conn = get_connection(db_path)
    try:
        sources = get_active_source_files(conn, "accounting")
        print(f"Active accounting source files: {len(sources)}")
        if not sources:
            print("Nothing to do.")
            return 0

        stredisko_map = get_stredisko_map(conn)

        before_neg = conn.execute(
            "SELECT COUNT(*) FROM accounting_entries WHERE castka < 0"
        ).fetchone()[0]
        print(f"Before reimport: {before_neg} entries with castka < 0")

        total_entries = 0
        for s in sources:
            entries = load_hlavni_kniha_from_bytes(s["content"], stredisko_map or None)
            n_neg = sum(1 for e in entries if (e.get("castka") or 0) < 0)
            total_entries += len(entries)
            print(
                f"  source_file_id={s['id']!s:>4}  "
                f"name={s['original_name']!r}  "
                f"entries={len(entries)}  negatives={n_neg}"
            )
            if not dry_run:
                save_accounting_entries(conn, entries, int(s["id"]), commit=False)
        if not dry_run:
            conn.commit()

        after_neg = conn.execute(
            "SELECT COUNT(*) FROM accounting_entries WHERE castka < 0"
        ).fetchone()[0]
        print(f"After reimport:  {after_neg} entries with castka < 0")
        print(f"Total entries processed: {total_entries}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and report counts without writing to DB.",
    )
    args = parser.parse_args()
    sys.exit(main(args.dry_run))
