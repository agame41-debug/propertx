"""
Sync Hostify listings into DB-backed report objects as safe draft inventory.

Usage:
    python -m report.hostify_inventory sync
    python -m report.hostify_inventory sync --activate-new
    python -m report.hostify_inventory sync --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from typing import Callable

from hostify_api import HostifyHttpError, hostify_get
from report.config import get_all_properties, load_runtime_config, sync_property_to_db
from report.db import get_connection

_DEFAULT_ALIAS_VALID_FROM = "0001-01-01"
_BOOKING_HINTS = ("booking", "bcom")


def _normalized_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    ascii_text = unicodedata.normalize("NFKD", text)
    ascii_text = ascii_text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).strip().lower()


def _slugify_listing(nickname: str, listing_id: int | str) -> str:
    base = str(nickname or "").strip()
    if not base:
        base = f"Hostify {listing_id}"
    normalized = unicodedata.normalize("NFKD", base)
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    tokens = re.split(r"[^A-Za-z0-9]+", normalized)
    clean = []
    for token in tokens:
        if not token:
            continue
        clean.append(token if token.isdigit() else token[0].upper() + token[1:])
    return "_".join(clean) or f"Hostify_{listing_id}"


def _dedupe_slug(base_slug: str, used_slugs: set[str]) -> str:
    candidate = base_slug
    suffix = 2
    while candidate in used_slugs:
        candidate = f"{base_slug}_{suffix}"
        suffix += 1
    used_slugs.add(candidate)
    return candidate


def _guess_child_channel(child: dict) -> str:
    nickname = _normalized_text(child.get("nickname"))
    name = _normalized_text(child.get("name"))
    fs_integration_type = int(child.get("fs_integration_type") or 0)

    if any(hint in nickname or hint in name for hint in _BOOKING_HINTS):
        return "booking"
    if fs_integration_type == 22:
        return "booking"
    if fs_integration_type == 1:
        return "airbnb"
    return ""


def _match_existing_property(listing: dict, existing_properties: list[dict]) -> dict | None:
    listing_id = int(listing.get("id") or 0)
    nickname_key = _normalized_text(listing.get("nickname"))
    display_key = _normalized_text(listing.get("name"))

    for prop in existing_properties:
        if int(prop.get("listing_id") or 0) == listing_id and listing_id:
            return prop

    for prop in existing_properties:
        if nickname_key and _normalized_text(prop.get("listing_nickname")) == nickname_key:
            return prop

    for prop in existing_properties:
        if display_key and _normalized_text(prop.get("display_name")) == display_key:
            return prop

    return None


def _build_synced_channels(existing_prop: dict, listing: dict, children: list[dict]) -> dict:
    channels = json.loads(json.dumps(existing_prop.get("channels") or {}))
    channels.setdefault("hostify", {})
    channels.setdefault("airbnb", {})
    channels.setdefault("booking", {})

    # Hostify child listing nicknames should mirror the current Hostify source of truth.
    hostify_names: list[str] = []
    airbnb_names = [
        str(name).strip()
        for name in (channels["airbnb"].get("listing_names") or [])
        if str(name).strip()
    ]
    booking_nickname = str(channels["booking"].get("listing_nickname") or "").strip()
    booking_property_id = str(channels["booking"].get("property_id") or "").strip()

    parent_name = str(listing.get("name") or "").strip()
    if parent_name and parent_name not in airbnb_names:
        airbnb_names.append(parent_name)

    for child in children:
        channel = _guess_child_channel(child)
        child_name = str(child.get("name") or "").strip()
        child_nickname = str(child.get("nickname") or "").strip()
        child_channel_id = str(child.get("channel_listing_id") or "").strip()

        if child_nickname and child_nickname != str(listing.get("nickname") or "").strip():
            if child_nickname not in hostify_names:
                hostify_names.append(child_nickname)

        if channel == "airbnb":
            if child_name and child_name not in airbnb_names:
                airbnb_names.append(child_name)
        elif channel == "booking":
            if not booking_nickname and child_nickname:
                booking_nickname = child_nickname
            if not booking_property_id and child_channel_id:
                booking_property_id = child_channel_id

    channels["hostify"]["listing_names"] = hostify_names
    channels["airbnb"]["listing_names"] = airbnb_names
    channels["booking"]["listing_nickname"] = booking_nickname
    channels["booking"]["property_id"] = booking_property_id
    return channels


def fetch_hostify_listings(
    service_pms: int,
    *,
    max_pages: int = 100,
    fetcher: Callable[..., dict] | None = None,
) -> list[dict]:
    fetch = fetcher or hostify_get
    listings: list[dict] = []
    page = 1
    while page <= max_pages:
        payload = fetch("listings", params={"service_pms": int(service_pms), "page": page})
        rows = list(payload.get("listings") or [])
        listings.extend(rows)
        if not payload.get("next_page") or not rows:
            break
        page += 1
    return listings


def fetch_hostify_children(
    listing_id: int,
    *,
    fetcher: Callable[..., dict] | None = None,
) -> list[dict]:
    fetch = fetcher or hostify_get
    payload = fetch(f"listings/children/{int(listing_id)}")
    return list(payload.get("listings") or [])


def sync_hostify_inventory(
    conn,
    *,
    existing_properties: list[dict] | None = None,
    list_fetcher: Callable[[int], list[dict]] | None = None,
    children_fetcher: Callable[[int], list[dict]] | None = None,
    activate_new: bool = False,
    alias_valid_from: str = _DEFAULT_ALIAS_VALID_FROM,
    dry_run: bool = False,
) -> dict:
    props = list(existing_properties or [])
    if not props:
        config = load_runtime_config(None, db_conn=conn)
        props = get_all_properties(config)

    list_fetch = list_fetcher or (lambda service_pms: fetch_hostify_listings(service_pms))
    child_fetch = children_fetcher or (lambda listing_id: fetch_hostify_children(listing_id))

    parent_listings = list_fetch(1)
    used_slugs = {str(prop.get("slug") or "") for prop in props if str(prop.get("slug") or "").strip()}

    summary = {
        "parents_total": len(parent_listings),
        "children_total": 0,
        "created": 0,
        "updated": 0,
        "draft_created": 0,
        "active_preserved": 0,
        "activated_new": 0,
        "objects": [],
    }

    for listing in parent_listings:
        existing_prop = _match_existing_property(listing, props)
        listing_id = int(listing.get("id") or 0)
        nickname = str(listing.get("nickname") or "").strip()
        display_name = str(
            (existing_prop or {}).get("display_name")
            or nickname
            or listing.get("name")
            or listing_id
        )

        if existing_prop:
            slug = str(existing_prop["slug"])
            used_slugs.add(slug)
        else:
            slug = _dedupe_slug(_slugify_listing(nickname, listing_id), used_slugs)

        children = child_fetch(listing_id)
        summary["children_total"] += len(children)
        channels = _build_synced_channels(existing_prop or {}, listing, children)

        # Preserve old nicknames as aliases when Hostify renames a listing.
        if existing_prop and nickname:
            old_nickname = str(existing_prop.get("listing_nickname") or "").strip()
            if old_nickname and old_nickname != nickname:
                hostify_names = channels.get("hostify", {}).get("listing_names") or []
                if old_nickname not in hostify_names:
                    hostify_names.append(old_nickname)
                    channels.setdefault("hostify", {})["listing_names"] = hostify_names
                summary.setdefault("renamed", []).append(
                    {"slug": slug, "old": old_nickname, "new": nickname}
                )
            old_booking = str(
                (existing_prop.get("channels") or {}).get("booking", {}).get("listing_nickname") or ""
            ).strip()
            new_booking = str(channels.get("booking", {}).get("listing_nickname") or "").strip()
            if old_booking and old_booking != new_booking:
                hostify_names = channels.get("hostify", {}).get("listing_names") or []
                if old_booking not in hostify_names:
                    hostify_names.append(old_booking)
                    channels.setdefault("hostify", {})["listing_names"] = hostify_names

        active = bool(existing_prop.get("active", True)) if existing_prop else False
        if not existing_prop and activate_new:
            active = True

        prop = dict(existing_prop or {})
        prop.update(
            {
                "slug": slug,
                "display_name": display_name,
                "listing_id": listing_id,
                "listing_nickname": nickname or str(prop.get("listing_nickname") or "").strip(),
                # Safe defaults for draft imports; require review before activation.
                "balicky_per_person": prop.get("balicky_per_person", 0),
                "city_tax_rate": prop.get("city_tax_rate", 50),
                "vat_rate": prop.get("vat_rate", 0.21),
                "rentero_commission": prop.get("rentero_commission", 0.15),
                "active": active,
                "channels": channels,
            }
        )

        if not dry_run:
            sync_property_to_db(
                conn,
                slug,
                prop,
                replace_aliases=False,
                alias_valid_from=alias_valid_from,
            )

        if existing_prop:
            summary["updated"] += 1
            if active:
                summary["active_preserved"] += 1
        else:
            summary["created"] += 1
            if active:
                summary["activated_new"] += 1
            else:
                summary["draft_created"] += 1
            props.append(prop)

        summary["objects"].append(
            {
                "slug": slug,
                "listing_id": listing_id,
                "display_name": display_name,
                "listing_nickname": prop["listing_nickname"],
                "active": active,
                "booking_property_id": str((channels.get("booking") or {}).get("property_id") or ""),
                "booking_listing_nickname": str((channels.get("booking") or {}).get("listing_nickname") or ""),
                "airbnb_listing_names": list((channels.get("airbnb") or {}).get("listing_names") or []),
                "children_count": len(children),
            }
        )

    return summary


def _print_summary(summary: dict) -> None:
    print(
        "Synced Hostify inventory:",
        f"parents={summary['parents_total']}",
        f"children={summary['children_total']}",
        f"created={summary['created']}",
        f"updated={summary['updated']}",
        f"drafts={summary['draft_created']}",
        f"active_preserved={summary['active_preserved']}",
        f"activated_new={summary['activated_new']}",
    )
    for rename in summary.get("renamed", []):
        print(f" RENAMED: {rename['slug']}: {rename['old']!r} -> {rename['new']!r} (old preserved as alias)")
    for item in summary.get("objects", [])[:20]:
        state = "active" if item.get("active") else "draft"
        print(
            f" - {item['slug']} [{state}] listing_id={item['listing_id']} "
            f"children={item['children_count']} booking={item['booking_property_id'] or '-'}"
        )
    if len(summary.get("objects", [])) > 20:
        print(f" ... and {len(summary['objects']) - 20} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync Hostify listings into DB-backed report objects")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="Sync Hostify PMS listings into report_objects")
    sync_parser.add_argument("--activate-new", action="store_true", help="Create new objects as active instead of draft.")
    sync_parser.add_argument("--dry-run", action="store_true", help="Preview the sync summary without writing to SQLite.")

    args = parser.parse_args(argv)

    if args.command != "sync":
        parser.error(f"Unknown command: {args.command}")

    conn = get_connection()
    try:
        summary = sync_hostify_inventory(
            conn,
            activate_new=args.activate_new,
            dry_run=args.dry_run,
        )
    except HostifyHttpError as exc:
        print(f"ERROR: Hostify sync failed: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
