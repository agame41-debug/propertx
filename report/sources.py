"""
CLI for manual source-file imports into SQLite.

Examples:
    python -m report.sources import --type airbnb --path source/airbnb/file.csv
    python -m report.sources list
    python -m report.sources deactivate --id 3
"""

from __future__ import annotations

import argparse
import sys

from report.source_registry import (
    SOURCE_TYPES,
    fetch_source_listing,
    import_local_file,
    mark_source_active,
)


def _cmd_import(args: argparse.Namespace) -> int:
    file_id = import_local_file(
        args.path,
        args.source_type,
        active=not args.inactive,
        imported_by="cli",
    )
    print(f"Imported source file #{file_id}: {args.source_type} <- {args.path}")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    rows = fetch_source_listing(args.source_type, active_only=args.active_only)
    if not rows:
        print("No source files registered.")
        return 0
    for row in rows:
        active = "active" if row.get("is_active") else "inactive"
        print(
            f"#{row['id']} {row['source_type']} {active} "
            f"{row['original_name']} ({row.get('size_bytes', 0)} bytes) "
            f"{row['imported_at']}"
        )
    return 0


def _cmd_toggle(args: argparse.Namespace, is_active: bool) -> int:
    mark_source_active(args.file_id, is_active)
    state = "active" if is_active else "inactive"
    print(f"Source file #{args.file_id} marked {state}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage DB-backed report source files")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="Import a local file into SQLite")
    p_import.add_argument("--type", dest="source_type", required=True, choices=sorted(SOURCE_TYPES))
    p_import.add_argument("--path", required=True, help="Local path to file")
    p_import.add_argument("--inactive", action="store_true", help="Store but keep inactive")
    p_import.set_defaults(func=_cmd_import)

    p_list = sub.add_parser("list", help="List registered source files")
    p_list.add_argument("--type", dest="source_type", choices=sorted(SOURCE_TYPES))
    p_list.add_argument("--active-only", action="store_true")
    p_list.set_defaults(func=_cmd_list)

    p_deactivate = sub.add_parser("deactivate", help="Deactivate a source file")
    p_deactivate.add_argument("--id", dest="file_id", type=int, required=True)
    p_deactivate.set_defaults(func=lambda args: _cmd_toggle(args, False))

    p_activate = sub.add_parser("activate", help="Activate a source file")
    p_activate.add_argument("--id", dest="file_id", type=int, required=True)
    p_activate.set_defaults(func=lambda args: _cmd_toggle(args, True))

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
