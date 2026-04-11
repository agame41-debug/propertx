"""
CLI for migrating JSON property config into DB-backed runtime config tables.

Examples:
    python -m report.config_migration
    python -m report.config_migration --config config/properties.json --replace-aliases
"""

from __future__ import annotations

import argparse
import sys

from report.config import load_config, sync_json_config_to_db
from report.db import get_connection


def main() -> int:
    parser = argparse.ArgumentParser(description="Import properties.json into DB-backed runtime config")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to properties.json [default: config/properties.json]",
    )
    parser.add_argument(
        "--replace-aliases",
        action="store_true",
        help="Replace current active alias sets while preserving historical alias rows.",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    conn = get_connection()
    try:
        sync_json_config_to_db(conn, config, replace_aliases=args.replace_aliases)
    finally:
        conn.close()

    count = len((config.get("properties") or {}).keys())
    print(f"Migrated {count} propertie(s) into DB-backed runtime config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
