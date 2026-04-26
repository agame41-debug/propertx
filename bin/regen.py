#!/usr/bin/env python3
"""Rentero admin CLI — regenerate report data via the in-process engine.

Replaces the legacy `python -m report.main` invocation. No Excel output,
no subprocess gymnastics — straight to engine.generate_report_in_process,
the same code path the web UI uses.

Usage:
    bin/regen.py SLUG YEAR MONTH         — regenerate one property/month
    bin/regen.py --all YEAR MONTH         — regenerate every active property

Environment:
    Loads .env automatically. RENTERO_USERNAME / RENTERO_PASSWORD /
    RENTERO_SESSION_SECRET are not required for CLI use; the engine
    operates directly on cache/rentero.db.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date

# Ensure project root is on sys.path when invoked as `python bin/regen.py`
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regenerate Rentero report data for one or all properties.",
    )
    parser.add_argument(
        "slug",
        nargs="?",
        help="Property slug. Required unless --all is given.",
    )
    parser.add_argument(
        "year",
        type=int,
        help="Year (e.g. 2026).",
    )
    parser.add_argument(
        "month",
        type=int,
        help="Month 1-12.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Regenerate every active property for the given month.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose log output.",
    )
    args = parser.parse_args(argv)

    today = date.today()
    if args.year < 2020 or args.year > today.year + 2:
        parser.error(f"year out of range: {args.year}")
    if args.month < 1 or args.month > 12:
        parser.error(f"month must be 1-12, got {args.month}")
    if args.all and args.slug is not None:
        parser.error("--all is incompatible with a slug argument")
    if not args.all and args.slug is None:
        parser.error("provide a slug or --all")
    return args


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _regen_one(conn, slug: str, year: int, month: int, config: dict) -> dict:
    from report.engine import generate_report_in_process

    return generate_report_in_process(conn, slug, year, month, config)


def main(argv: list[str] | None = None) -> int:
    try:
        from dotenv import load_dotenv
        _env_path = os.path.join(_PROJECT_ROOT, ".env")
        if os.path.exists(_env_path):
            load_dotenv(_env_path)
    except ImportError:
        pass
    args = parse_args(argv)
    _setup_logging(args.verbose)
    log = logging.getLogger("regen")

    from report.config import load_runtime_config
    from report.db import get_connection

    conn = get_connection()
    try:
        config = load_runtime_config(
            os.path.join(_PROJECT_ROOT, "config", "properties.json"),
            db_conn=conn,
        )

        if args.all:
            from report.config import get_all_properties

            slugs = [p["slug"] for p in get_all_properties(config) if p.get("active", True)]
            log.info("Regenerating %d properties for %d-%02d", len(slugs), args.year, args.month)
            ok = skipped = failed = 0
            for slug in slugs:
                try:
                    result = _regen_one(conn, slug, args.year, args.month, config)
                    if result.get("skipped"):
                        skipped += 1
                        log.info("  %-30s SKIPPED (%s)", slug, result.get("reason"))
                    else:
                        ok += 1
                        log.info("  %-30s OK (%d rows)", slug, result.get("rows_count", 0))
                except Exception as exc:
                    failed += 1
                    log.exception("  %-30s FAILED: %s", slug, exc)
            log.info("Done. ok=%d skipped=%d failed=%d", ok, skipped, failed)
            return 1 if failed else 0

        result = _regen_one(conn, args.slug, args.year, args.month, config)
        if result.get("skipped"):
            log.info("%s %d-%02d SKIPPED (%s)", args.slug, args.year, args.month, result.get("reason"))
        else:
            log.info("%s %d-%02d OK (%d rows)", args.slug, args.year, args.month, result.get("rows_count", 0))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
