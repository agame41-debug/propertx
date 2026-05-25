"""
report/config.py — Load and access per-property configuration.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime

from report.db import (
    get_report_object_aliases,
    get_report_object_channel_configs,
    list_report_objects,
    save_report_object_channel_config,
    set_report_object_aliases,
    upsert_report_object,
)

_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "properties.json"
)


def _load_json_config(config_path: str | None = None) -> dict:
    path = config_path or _DEFAULT_CONFIG_PATH
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Malformed config JSON: {e}") from e
    if "properties" not in data:
        raise ValueError("Config missing top-level 'properties' key.")
    return data


def load_config(config_path: str | None = None) -> dict:
    """
    Load config/properties.json.
    Raises FileNotFoundError if missing, ValueError if malformed.
    """
    return _load_json_config(config_path)


def _merge_channel_aliases(channels: dict, alias_rows: list[dict]) -> dict:
    merged = json.loads(json.dumps(channels or {}))

    for row in alias_rows:
        if not row.get("is_active"):
            continue
        channel = (row.get("channel") or "").strip()
        alias_type = (row.get("alias_type") or "").strip()
        alias_value = (row.get("alias_value") or "").strip()
        if not channel or not alias_type or not alias_value:
            continue

        channel_cfg = merged.setdefault(channel, {})
        if channel == "airbnb" and alias_type == "listing_name":
            names = channel_cfg.get("listing_names") or []
            names = [name for name in names if name]
            if alias_value not in names:
                names.append(alias_value)
            channel_cfg["listing_names"] = names
        elif channel == "booking" and alias_type == "property_id":
            channel_cfg["property_id"] = alias_value
        elif channel == "booking" and alias_type == "listing_nickname":
            channel_cfg["listing_nickname"] = alias_value

    return merged


def _parse_alias_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    end = next_month.fromordinal(next_month.toordinal() - 1)
    return start, end


def _alias_valid_for_month(alias_row: dict, year: int, month: int) -> bool:
    month_start, month_end = _month_bounds(year, month)
    valid_from = _parse_alias_date(alias_row.get("valid_from"))
    valid_to = _parse_alias_date(alias_row.get("valid_to"))
    if valid_from and valid_from > month_end:
        return False
    if valid_to and valid_to < month_start:
        return False
    return True


def _get_alias_history(prop: dict) -> list[dict]:
    return list(prop.get("_alias_history") or [])


def _get_alias_rows(
    prop: dict,
    channel: str,
    alias_type: str,
    *,
    year: int | None = None,
    month: int | None = None,
) -> list[dict]:
    rows = [
        row for row in _get_alias_history(prop)
        if row.get("channel") == channel and row.get("alias_type") == alias_type
    ]
    if year and month:
        rows = [row for row in rows if _alias_valid_for_month(row, year, month)]
    elif rows:
        rows = [row for row in rows if row.get("is_active")]
    rows.sort(
        key=lambda row: (
            _parse_alias_date(row.get("valid_from")) or date.min,
            row.get("created_at") or "",
            int(row.get("id") or 0),
        )
    )
    return rows


def _resolve_multi_alias_values(
    prop: dict,
    channel: str,
    alias_type: str,
    *,
    year: int | None = None,
    month: int | None = None,
) -> list[str]:
    rows = _get_alias_rows(prop, channel, alias_type, year=year, month=month)
    values = [str(row.get("alias_value") or "").strip() for row in rows if str(row.get("alias_value") or "").strip()]
    return list(dict.fromkeys(values))


def _resolve_scalar_alias_value(
    prop: dict,
    channel: str,
    alias_type: str,
    *,
    year: int | None = None,
    month: int | None = None,
) -> str:
    values = _resolve_multi_alias_values(prop, channel, alias_type, year=year, month=month)
    return values[-1] if values else ""


def _build_property_from_db(
    slug: str,
    base: dict,
    db_object: dict,
    channel_configs: list[dict],
    alias_rows_active: list[dict],
    alias_rows_all: list[dict],
) -> dict:
    prop = dict(base or {})
    prop.update(
        {
            "listing_id": db_object.get("hostify_listing_id"),
            "listing_nickname": db_object.get("listing_nickname") or prop.get("listing_nickname", ""),
            "display_name": db_object.get("display_name") or prop.get("display_name") or db_object.get("listing_nickname", ""),
            "balicky_per_person": db_object.get("balicky_per_person", prop.get("balicky_per_person", 0)),
            "city_tax_rate": db_object.get("city_tax_rate", prop.get("city_tax_rate", 0)),
            "vat_rate": db_object.get("vat_rate", prop.get("vat_rate", 0.21)),
            "rentero_commission": db_object.get("rentero_commission", prop.get("rentero_commission", 0.15)),
            "client_type": db_object.get("client_type", prop.get("client_type", "rentero")),
            "active": bool(db_object.get("active", 1)),
            "slug": slug,
        }
    )

    channels = json.loads(json.dumps(prop.get("channels") or {}))
    for row in channel_configs:
        channels[row["channel"]] = row["config"]
    prop["channels"] = _merge_channel_aliases(channels, alias_rows_active)
    prop["_alias_history"] = [dict(row) for row in alias_rows_all]
    return prop


def load_runtime_config(
    config_path: str | None = None,
    *,
    db_conn=None,
) -> dict:
    """
    Load operational config.

    If DB-backed config exists, it overrides JSON per object. JSON remains a
    bootstrap/fallback source during the transition.
    """
    load_error: Exception | None = None
    try:
        data = _load_json_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        data = {"properties": {}}
        load_error = exc
    properties = {
        slug: {**prop, "slug": slug}
        for slug, prop in data.get("properties", {}).items()
    }

    if db_conn is None:
        if load_error is not None:
            raise load_error
        data["properties"] = {
            slug: {k: v for k, v in prop.items() if k != "slug"}
            for slug, prop in properties.items()
        }
        return data

    db_objects = list_report_objects(db_conn)
    if not db_objects:
        if load_error is not None:
            raise load_error
        data["properties"] = {
            slug: {k: v for k, v in prop.items() if k != "slug"}
            for slug, prop in properties.items()
        }
        return data

    channel_configs = {}
    for row in get_report_object_channel_configs(db_conn):
        channel_configs.setdefault(row["report_object_slug"], []).append(row)

    alias_rows_active = {}
    for row in get_report_object_aliases(db_conn, include_inactive=False):
        alias_rows_active.setdefault(row["report_object_slug"], []).append(row)
    alias_rows_all = {}
    for row in get_report_object_aliases(db_conn, include_inactive=True):
        alias_rows_all.setdefault(row["report_object_slug"], []).append(row)

    for obj in db_objects:
        slug = obj["slug"]
        base = properties.get(slug, {})
        properties[slug] = _build_property_from_db(
            slug,
            base,
            obj,
            channel_configs.get(slug, []),
            alias_rows_active.get(slug, []),
            alias_rows_all.get(slug, []),
        )

    data["properties"] = {
        slug: {k: v for k, v in prop.items() if k != "slug"}
        for slug, prop in properties.items()
    }
    return data


def sync_property_to_db(
    conn,
    slug: str,
    prop: dict,
    *,
    replace_aliases: bool = False,
    alias_valid_from: str | None = None,
) -> None:
    """
    Persist one property config into DB-backed runtime config tables.
    """
    upsert_report_object(
        conn,
        {
            "slug": slug,
            "display_name": prop.get("display_name") or prop.get("listing_nickname", ""),
            "hostify_listing_id": prop.get("listing_id"),
            "listing_nickname": prop.get("listing_nickname", ""),
            "balicky_per_person": prop.get("balicky_per_person", 0),
            "city_tax_rate": prop.get("city_tax_rate", 0),
            "vat_rate": prop.get("vat_rate", 0.21),
            "rentero_commission": prop.get("rentero_commission", 0.15),
            "client_type": prop.get("client_type", "rentero"),
            "active": prop.get("active", True),
        },
    )

    channels = prop.get("channels") or {}
    for channel, channel_cfg in channels.items():
        save_report_object_channel_config(conn, slug, channel, channel_cfg)

    existing_aliases = get_report_object_aliases(conn, slug, include_inactive=True)
    existing_keys = {
        (row["channel"], row["alias_type"])
        for row in existing_aliases
    }

    alias_payloads = [
        (
            "hostify",
            "listing_nickname",
            list(
                dict.fromkeys(
                    [
                        str(prop.get("listing_nickname", "") or "").strip(),
                        *[
                            str(item or "").strip()
                            for item in ((channels.get("hostify") or {}).get("listing_names") or [])
                        ],
                    ]
                )
            ),
        ),
        ("airbnb", "listing_name", list((channels.get("airbnb") or {}).get("listing_names") or [])),
        (
            "booking",
            "listing_nickname",
            [(channels.get("booking") or {}).get("listing_nickname", "")],
        ),
        (
            "booking",
            "property_id",
            [str((channels.get("booking") or {}).get("property_id", "") or "")],
        ),
    ]

    for channel, alias_type, values in alias_payloads:
        if replace_aliases or (channel, alias_type) not in existing_keys:
            set_report_object_aliases(
                conn,
                slug,
                channel,
                alias_type,
                values,
                valid_from=alias_valid_from,
            )
        else:
            # Merge: add new values without removing existing ones.
            existing_values = {
                row["alias_value"]
                for row in existing_aliases
                if row["channel"] == channel
                and row["alias_type"] == alias_type
                and row["is_active"]
            }
            merged = list(dict.fromkeys([*existing_values, *values]))
            if set(merged) != existing_values:
                set_report_object_aliases(
                    conn,
                    slug,
                    channel,
                    alias_type,
                    merged,
                    valid_from=alias_valid_from,
                )


def sync_json_config_to_db(
    conn,
    config: dict,
    *,
    replace_aliases: bool = False,
) -> None:
    for slug, prop in (config.get("properties") or {}).items():
        sync_property_to_db(
            conn,
            slug,
            prop,
            replace_aliases=replace_aliases,
            alias_valid_from="0001-01-01",
        )


def get_property_config(listing_id: int | str, config: dict) -> dict:
    """
    Look up property by listing_id. Returns property dict with its slug merged in.
    Raises KeyError if not found.
    """
    lid = int(listing_id)
    for slug, prop in config["properties"].items():
        if int(prop.get("listing_id", -1)) == lid:
            return {**prop, "slug": slug}
    raise KeyError(f"No property configured for listing_id={listing_id}")


def resolve_property_config(conn, slug: str, year: int, month: int, config: dict) -> dict:
    """Return the property config for a slug as of (year, month).

    Starts from the base config (channels / aliases / identity) and overlays the
    month-resolved profile segment (owner + client_type + rates + active + středisko).
    Falls back to base values when no segment exists (legacy DBs).
    """
    from report.db_object_profiles import get_object_profile

    prop = dict((config.get("properties") or {}).get(slug) or {})
    prop["slug"] = slug
    seg = get_object_profile(conn, slug, year, month)
    if seg:
        for f in ("client_type", "city_tax_rate", "balicky_per_person",
                  "vat_rate", "rentero_commission"):
            if seg.get(f) is not None:
                prop[f] = seg[f]
        prop["active"] = bool(seg.get("active", prop.get("active", True)))
        prop["stredisko"] = seg.get("stredisko") or prop.get("stredisko", "")
        prop["owner"] = {
            "name": seg.get("owner_name", ""), "ico": seg.get("ico", ""),
            "dic": seg.get("dic", ""), "platce_dph": seg.get("platce_dph", 0),
            "adresa": seg.get("adresa", ""), "bank_account": seg.get("bank_account", ""),
            "email": seg.get("email", ""), "phone": seg.get("phone", ""),
            "notes": seg.get("notes", ""),
        }
    return prop


def get_all_slugs(config: dict, *, active_only: bool = False) -> list[str]:
    """Return all property slug keys."""
    return [
        slug
        for slug, prop in config["properties"].items()
        if not active_only or bool(prop.get("active", True))
    ]


def get_all_properties(config: dict, *, active_only: bool = False) -> list[dict]:
    """Return list of all property dicts with slug merged in."""
    return [
        {**prop, "slug": slug}
        for slug, prop in config["properties"].items()
        if not active_only or bool(prop.get("active", True))
    ]


def get_hostify_listing_names(
    prop: dict,
    *,
    year: int | None = None,
    month: int | None = None,
) -> list[str]:
    """
    Return Hostify listing nickname aliases valid for the requested month.
    """
    alias_names = _resolve_multi_alias_values(
        prop, "hostify", "listing_nickname", year=year, month=month
    )
    names: list[str] = []
    names.extend(alias_names)
    hostify_channel = (prop.get("channels") or {}).get("hostify") or {}
    names.extend(
        str(name).strip()
        for name in (hostify_channel.get("listing_names") or [])
        if str(name).strip()
    )
    nickname = str(prop.get("listing_nickname") or "").strip()
    if nickname and not alias_names:
        names.append(nickname)
    return list(dict.fromkeys(names))


def get_airbnb_listing_names(
    prop: dict,
    *,
    year: int | None = None,
    month: int | None = None,
) -> list[str]:
    """
    Return Airbnb listing name aliases for a property.
    Reads from channels.airbnb.listing_names if present.
    Falls back to listing_nickname.
    """
    names = _resolve_multi_alias_values(prop, "airbnb", "listing_name", year=year, month=month)
    if names:
        return names
    channels = prop.get("channels") or {}
    airbnb = channels.get("airbnb") or {}
    names = airbnb.get("listing_names") or []
    if names:
        return list(names)
    nickname = prop.get("listing_nickname", "")
    return [nickname] if nickname else []


def get_booking_config(
    prop: dict,
    *,
    year: int | None = None,
    month: int | None = None,
) -> dict:
    """
    Return Booking channel config for a property.
    Reads from channels.booking if present, falls back to root-level fields.
    Returns dict with keys: listing_nickname, property_id.
    """
    alias_listing_nickname = _resolve_scalar_alias_value(
        prop, "booking", "listing_nickname", year=year, month=month
    )
    alias_property_id = _resolve_scalar_alias_value(
        prop, "booking", "property_id", year=year, month=month
    )
    channels = prop.get("channels") or {}
    booking = channels.get("booking") or {}
    return {
        "listing_nickname": alias_listing_nickname or booking.get("listing_nickname") or prop.get("booking_listing_nickname", ""),
        "property_id": alias_property_id or booking.get("property_id") or prop.get("booking_property_id", ""),
    }
