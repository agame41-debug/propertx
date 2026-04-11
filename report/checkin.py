"""
report/checkin.py — Checkin guest report parsing and city-tax overrides.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from datetime import date, datetime

from report.loader import assign_report_month

from report.config import get_booking_config, get_hostify_listing_names

logger = logging.getLogger(__name__)

_EXPECTED_HEADER = [
    "Property Name",
    "Full Name",
    "Nationality",
    "ID Type",
    "ID Number",
    "Phone Number",
    "Check-Out Date",
    "Reservation ID",
    "Check-In Date",
    "Name",
    "Surname",
    "Birth Country",
    "Residence Country",
    "Guest Age",
]


def _normalize_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = "".join(ch if ch.isalnum() else " " for ch in text.casefold())
    return " ".join(text.split())


def _decode_source_content(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "latin1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _load_source_text(source) -> tuple[str, str]:
    if isinstance(source, str):
        with open(source, "rb") as fh:
            content = fh.read()
        return source, _decode_source_content(content)
    content = source.get("content") or b""
    if isinstance(content, memoryview):
        content = content.tobytes()
    return str(source.get("original_name") or f"db:{source.get('id', '?')}"), _decode_source_content(bytes(content))


def _parse_checkin_date(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _iter_checkin_guest_rows(sources: list) -> list[dict]:
    rows: list[dict] = []
    for source in sources:
        source_name, text = _load_source_text(source)
        lines = [line.strip("\ufeff") for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        header = [part.strip() for part in lines[0].split(";")]
        if header[: len(_EXPECTED_HEADER)] != _EXPECTED_HEADER:
            logger.warning("Checkin file %s has unexpected header: %s", source_name, header)
            continue
        for lineno, raw_line in enumerate(lines[1:], start=2):
            parts = [part.strip() for part in raw_line.split(";")]
            if len(parts) < len(_EXPECTED_HEADER):
                parts.extend([""] * (len(_EXPECTED_HEADER) - len(parts)))
            elif len(parts) > len(_EXPECTED_HEADER):
                parts = parts[: len(_EXPECTED_HEADER)]
            row = dict(zip(_EXPECTED_HEADER, parts))
            check_in = _parse_checkin_date(row["Check-In Date"])
            check_out = _parse_checkin_date(row["Check-Out Date"])
            reservation_id = row["Reservation ID"].strip()
            property_name = row["Property Name"].strip()
            if not reservation_id or not property_name or not check_in or not check_out:
                continue
            guest_age = None
            age_text = row["Guest Age"].strip()
            if age_text:
                try:
                    guest_age = int(float(age_text))
                except ValueError:
                    guest_age = None
            full_name = row["Full Name"].strip() or " ".join(
                part for part in (row["Name"].strip(), row["Surname"].strip()) if part
            ).strip()
            rows.append(
                {
                    "source_name": source_name,
                    "property_name": property_name,
                    "reservation_id": reservation_id,
                    "check_in": check_in.isoformat(),
                    "check_out": check_out.isoformat(),
                    "guest_name": full_name,
                    "guest_age": guest_age,
                }
            )
    return rows


def load_checkin_guest_rows(sources: list) -> list[dict]:
    """Return parsed guest rows sorted for deterministic persistence."""
    rows = _iter_checkin_guest_rows(sources)
    rows.sort(
        key=lambda item: (
            item["check_in"],
            item["check_out"],
            item["reservation_id"],
            _normalize_text(item.get("guest_name", "")),
        )
    )
    return rows


def _hydrate_checkin_group(group: dict) -> dict:
    guest_names = list(group.get("guest_names") or [])
    return {
        **group,
        "guest_names": guest_names,
        "_guest_names_normalized": {
            _normalize_text(name)
            for name in guest_names
            if _normalize_text(name)
        },
    }


def load_checkin_groups(sources: list) -> list[dict]:
    """
    Parse Checkin guest export and group guests by internal reservation id.

    Newer source files should be passed first; duplicate reservation ids keep the
    first-seen group.
    """
    grouped: dict[str, dict] = {}
    for row in load_checkin_guest_rows(sources):
        reservation_id = row["reservation_id"]
        group = grouped.get(reservation_id)
        if group is None:
            check_in = date.fromisoformat(row["check_in"])
            check_out = date.fromisoformat(row["check_out"])
            group = {
                "source_name": row["source_name"],
                "property_name": row["property_name"],
                "reservation_id": reservation_id,
                "check_in": row["check_in"],
                "check_out": row["check_out"],
                "stay_nights": max((check_out - check_in).days, 1),
                "guest_names": [],
                "_guest_names_normalized": set(),
                "total_guests": 0,
                "minor_guests": 0,
                "missing_age_guests": 0,
            }
            grouped[reservation_id] = group
        normalized_name = _normalize_text(row["guest_name"])
        if normalized_name and normalized_name not in group["_guest_names_normalized"]:
            group["_guest_names_normalized"].add(normalized_name)
            group["guest_names"].append(row["guest_name"])
        group["total_guests"] += 1
        guest_age = row.get("guest_age")
        if guest_age is None:
            group["missing_age_guests"] += 1
        elif guest_age < 18:
            group["minor_guests"] += 1

    results: list[dict] = []
    for reservation_id, group in grouped.items():
        stay_nights = int(group["stay_nights"] or 0)
        total_guests = int(group["total_guests"] or 0)
        if stay_nights > 60:
            paying_guests = 0
            exempt_guests = total_guests
        else:
            exempt_guests = int(group["minor_guests"] or 0) + int(group["missing_age_guests"] or 0)
            paying_guests = max(total_guests - exempt_guests, 0)
        results.append(
            _hydrate_checkin_group(
                {
                "source_name": group["source_name"],
                "property_name": group["property_name"],
                "reservation_id": reservation_id,
                "check_in": group["check_in"],
                "check_out": group["check_out"],
                "stay_nights": stay_nights,
                "guest_names": list(group["guest_names"]),
                "total_guests": total_guests,
                "paying_guests": paying_guests,
                "exempt_guests": exempt_guests,
                "missing_age_guests": int(group["missing_age_guests"] or 0),
                }
            )
        )
    results.sort(key=lambda item: (item["check_in"], item["check_out"], item["reservation_id"]))
    return results


def prepare_checkin_groups_for_storage(groups: list[dict], properties: list[dict]) -> list[dict]:
    prepared: list[dict] = []
    for group in groups:
        try:
            assigned_year, assigned_month = assign_report_month(
                date.fromisoformat(group["check_in"]),
                date.fromisoformat(group["check_out"]),
                int(group.get("stay_nights") or 0),
                "Checkin",
            )
        except Exception:
            assigned_year, assigned_month = None, None
        property_slug = ""
        if assigned_year is not None and assigned_month is not None:
            property_slug = match_checkin_property_slug(
                properties,
                group.get("property_name", ""),
                year=assigned_year,
                month=assigned_month,
            )
        prepared.append(
            {
                **group,
                "property_slug": property_slug,
                "assigned_year": assigned_year,
                "assigned_month": assigned_month,
                "guest_names_json": json.dumps(group.get("guest_names") or [], ensure_ascii=True),
            }
        )
    return prepared


def _property_names_for_checkin(property_config: dict, *, year: int | None = None, month: int | None = None) -> set[str]:
    names = {
        property_config.get("display_name", ""),
        property_config.get("listing_nickname", ""),
    }
    names.update(get_hostify_listing_names(property_config, year=year, month=month))
    booking_nickname = (get_booking_config(property_config, year=year, month=month).get("listing_nickname") or "").strip()
    if booking_nickname:
        names.add(booking_nickname)
    return {_normalize_text(name) for name in names if str(name or "").strip()}


def match_checkin_group_to_property(
    group: dict,
    property_config: dict,
    *,
    year: int | None = None,
    month: int | None = None,
) -> bool:
    stored_slug = str(group.get("property_slug") or "").strip()
    if stored_slug:
        return stored_slug == str(property_config.get("slug") or "").strip()
    return _normalize_text(group.get("property_name", "")) in _property_names_for_checkin(
        property_config,
        year=year,
        month=month,
    )


def match_checkin_property_slug(
    properties: list[dict],
    property_name: str,
    *,
    year: int | None = None,
    month: int | None = None,
) -> str:
    normalized = _normalize_text(property_name)
    if not normalized:
        return ""
    for prop in properties:
        if normalized in _property_names_for_checkin(prop, year=year, month=month):
            return str(prop.get("slug") or "")
    return ""


def hydrate_checkin_groups_from_db(rows: list[dict]) -> list[dict]:
    hydrated: list[dict] = []
    for row in rows:
        guest_names = row.get("guest_names")
        if guest_names is None:
            try:
                guest_names = json.loads(row.get("guest_names_json") or "[]")
            except json.JSONDecodeError:
                guest_names = []
        hydrated.append(
            _hydrate_checkin_group(
                {
                    **row,
                    "guest_names": guest_names,
                }
            )
        )
    hydrated.sort(key=lambda item: (item["check_in"], item["check_out"], item["reservation_id"]))
    return hydrated


def _city_tax_counts_from_row(row: dict) -> tuple[int, int]:
    paying = row.get("city_tax_paying_guests")
    exempt = row.get("city_tax_exempt_guests")
    if paying is not None or exempt is not None:
        return int(paying or 0), int(exempt or 0)
    adults = int(row.get("adults") or 0)
    exempt_guests = int(row.get("children") or 0) + int(row.get("infants") or 0)
    if "children_infants" in row and not row.get("children") and not row.get("infants"):
        exempt_guests = int(row.get("children_infants") or 0)
    return adults, exempt_guests


def _matched_row_payload(row: dict, group: dict) -> dict:
    age_complete = int(group.get("missing_age_guests") or 0) == 0
    return {
        **row,
        "checkin_verified": age_complete,
        "checkin_reservation_id": group["reservation_id"],
        "checkin_property_name": group["property_name"],
        "checkin_total_guests": group["total_guests"],
        "checkin_missing_age_guests": group["missing_age_guests"],
        "city_tax_paying_guests": group["paying_guests"],
        "city_tax_exempt_guests": group["exempt_guests"],
    }


def _reservation_audit_record(
    row: dict,
    *,
    match_status: str,
    overwritten_fields: dict | None = None,
    detail: dict | None = None,
) -> dict:
    before_paying, before_exempt = _city_tax_counts_from_row(row)
    return {
        "record_type": "reservation",
        "confirmation_code": row.get("confirmation_code", ""),
        "guest_name": row.get("guest_name", ""),
        "source": row.get("source", ""),
        "check_in": row.get("check_in", ""),
        "check_out": row.get("check_out", ""),
        "hostify_reservation_id": row.get("reservation_id", ""),
        "checkin_source_file_id": detail.get("checkin_source_file_id") if detail else None,
        "checkin_reservation_id": detail.get("checkin_reservation_id", "") if detail else "",
        "checkin_property_name": detail.get("checkin_property_name", "") if detail else "",
        "match_status": match_status,
        "before_paying_guests": before_paying,
        "before_exempt_guests": before_exempt,
        "after_paying_guests": detail.get("after_paying_guests", before_paying) if detail else before_paying,
        "after_exempt_guests": detail.get("after_exempt_guests", before_exempt) if detail else before_exempt,
        "checkin_total_guests": detail.get("checkin_total_guests") if detail else None,
        "checkin_missing_age_guests": detail.get("checkin_missing_age_guests") if detail else None,
        "overwritten_fields": overwritten_fields or {},
        "detail": detail or {},
    }


def _group_audit_record(
    group: dict,
    *,
    match_status: str,
    detail: dict | None = None,
) -> dict:
    return {
        "record_type": "evidence_group",
        "confirmation_code": detail.get("matched_confirmation_code", "") if detail else "",
        "guest_name": ", ".join(group.get("guest_names") or []),
        "source": "Checkin report",
        "check_in": group.get("check_in", ""),
        "check_out": group.get("check_out", ""),
        "hostify_reservation_id": detail.get("hostify_reservation_id", "") if detail else "",
        "checkin_source_file_id": group.get("source_file_id"),
        "checkin_reservation_id": group.get("reservation_id", ""),
        "checkin_property_name": group.get("property_name", ""),
        "match_status": match_status,
        "before_paying_guests": None,
        "before_exempt_guests": None,
        "after_paying_guests": group.get("paying_guests"),
        "after_exempt_guests": group.get("exempt_guests"),
        "checkin_total_guests": group.get("total_guests"),
        "checkin_missing_age_guests": group.get("missing_age_guests"),
        "overwritten_fields": {},
        "detail": detail or {},
    }


def apply_checkin_city_tax_overrides(
    reservations: list[dict],
    checkin_groups: list[dict],
    property_config: dict,
    *,
    year: int | None = None,
    month: int | None = None,
) -> tuple[list[dict], dict]:
    relevant_groups = [
        group for group in checkin_groups
        if match_checkin_group_to_property(group, property_config, year=year, month=month)
    ]
    updated: list[dict] = []
    active_rows: list[tuple[int, dict]] = []
    audit_records: list[dict] = []

    for idx, row in enumerate(reservations):
        if row.get("is_cancelled"):
            skipped = {
                **row,
                "tax_verification_required": False,
                "checkin_verified": False,
                "checkin_reservation_id": "",
                "checkin_property_name": "",
                "checkin_total_guests": None,
                "checkin_missing_age_guests": 0,
            }
            updated.append(skipped)
            audit_records.append(
                _reservation_audit_record(
                    skipped,
                    match_status="SKIPPED_CANCELLED",
                    detail={"reason": "Cancelled reservation does not require Checkin city-tax verification."},
                )
            )
            continue

        active = {**row, "tax_verification_required": True}
        updated.append(active)
        active_rows.append((idx, active))

    if not reservations and not relevant_groups:
        return reservations, {
            "matched": 0,
            "ambiguous_buckets": 0,
            "unmatched_groups": 0,
            "reservations_without_evidence": 0,
            "audit_records": audit_records,
        }
    if not active_rows:
        for group in relevant_groups:
            audit_records.append(
                _group_audit_record(
                    group,
                    match_status="UNMATCHED_GROUP",
                    detail={"reason": "No reservation row found for this bucket."},
                )
            )
        return updated, {
            "matched": 0,
            "ambiguous_buckets": 0,
            "unmatched_groups": len(relevant_groups),
            "reservations_without_evidence": 0,
            "audit_records": audit_records,
        }
    if not relevant_groups:
        for _idx, row in active_rows:
            audit_records.append(
                _reservation_audit_record(
                    row,
                    match_status="NO_EVIDENCE",
                    detail={"reason": "No evidence hostů group matched this property/month."},
                )
            )
        return updated, {
            "matched": 0,
            "ambiguous_buckets": 0,
            "unmatched_groups": 0,
            "reservations_without_evidence": len(active_rows),
            "audit_records": audit_records,
        }

    groups_by_bucket: dict[tuple[str, str], list[dict]] = {}
    for group in relevant_groups:
        groups_by_bucket.setdefault((group["check_in"], group["check_out"]), []).append(group)

    rows_by_bucket: dict[tuple[str, str], list[tuple[int, dict]]] = {}
    for idx, row in active_rows:
        rows_by_bucket.setdefault((row.get("check_in", ""), row.get("check_out", "")), []).append((idx, row))
    matched = 0
    ambiguous_buckets = 0
    reservations_without_evidence = 0

    for bucket, row_items in rows_by_bucket.items():
        group_items = groups_by_bucket.get(bucket) or []
        if not group_items:
            for _idx, row in row_items:
                reservations_without_evidence += 1
                audit_records.append(
                    _reservation_audit_record(
                        row,
                        match_status="NO_EVIDENCE",
                        detail={"reason": "No evidence hostů group for identical check-in/check-out dates."},
                    )
                )
            continue
        remaining_groups = list(group_items)
        matched_rows: dict[int, dict] = {}
        group_status: dict[str, dict] = {}

        for idx, row in sorted(row_items, key=lambda item: (str(item[1].get("reservation_id") or ""), str(item[1].get("confirmation_code") or ""))):
            normalized_guest = _normalize_text(row.get("guest_name", ""))
            if not normalized_guest:
                continue
            hits = [group for group in remaining_groups if normalized_guest in group.get("_guest_names_normalized", set())]
            if len(hits) != 1:
                continue
            group = hits[0]
            remaining_groups.remove(group)
            matched_rows[idx] = group
            updated[idx] = _matched_row_payload(updated[idx], group)
            matched += 1
            before_paying, before_exempt = _city_tax_counts_from_row(row)
            after_paying = int(group.get("paying_guests") or 0)
            after_exempt = int(group.get("exempt_guests") or 0)
            overwritten_fields = {}
            if before_paying != after_paying:
                overwritten_fields["city_tax_paying_guests"] = {"old": before_paying, "new": after_paying}
            if before_exempt != after_exempt:
                overwritten_fields["city_tax_exempt_guests"] = {"old": before_exempt, "new": after_exempt}
            detail = {
                "reason": "Unique guest-name match inside identical date bucket.",
                "checkin_reservation_id": group["reservation_id"],
                "checkin_source_file_id": group.get("source_file_id"),
                "checkin_property_name": group["property_name"],
                "checkin_total_guests": group["total_guests"],
                "checkin_missing_age_guests": group["missing_age_guests"],
                "after_paying_guests": after_paying,
                "after_exempt_guests": after_exempt,
            }
            audit_records.append(
                _reservation_audit_record(
                    row,
                    match_status="MATCHED",
                    overwritten_fields=overwritten_fields,
                    detail=detail,
                )
            )
            group_status[group["reservation_id"]] = {
                "status": "MATCHED",
                "detail": {
                    "reason": "Unique guest-name match.",
                    "matched_confirmation_code": row.get("confirmation_code", ""),
                    "hostify_reservation_id": row.get("reservation_id", ""),
                },
            }

        unmatched_rows = [item for item in row_items if item[0] not in matched_rows]
        if not unmatched_rows or not remaining_groups:
            for group in group_items:
                if group["reservation_id"] not in group_status:
                    group_status[group["reservation_id"]] = {
                        "status": "UNMATCHED_GROUP",
                        "detail": {"reason": "No reservation row remained in this bucket."},
                    }
            for group in group_items:
                status = group_status.get(group["reservation_id"], {"status": "UNMATCHED_GROUP", "detail": {}})
                audit_records.append(
                    _group_audit_record(
                        group,
                        match_status=status["status"],
                        detail=status["detail"],
                    )
                )
            continue
        if len(unmatched_rows) != len(remaining_groups):
            ambiguous_buckets += 1
            for _idx, row in unmatched_rows:
                reservations_without_evidence += 1
                audit_records.append(
                    _reservation_audit_record(
                        row,
                        match_status="AMBIGUOUS",
                        detail={"reason": "Ambiguous bucket: number of reservations and evidence groups differs."},
                    )
                )
            for group in remaining_groups:
                group_status[group["reservation_id"]] = {
                    "status": "AMBIGUOUS",
                    "detail": {"reason": "Ambiguous bucket: unresolved evidence group."},
                }
            for group in group_items:
                status = group_status.get(group["reservation_id"], {"status": "UNMATCHED_GROUP", "detail": {}})
                audit_records.append(
                    _group_audit_record(
                        group,
                        match_status=status["status"],
                        detail=status["detail"],
                    )
                )
            continue

        remaining_groups.sort(key=lambda item: item["reservation_id"])
        unmatched_rows.sort(
            key=lambda item: (
                str(item[1].get("reservation_id") or ""),
                str(item[1].get("confirmation_code") or ""),
                _normalize_text(item[1].get("guest_name", "")),
            )
        )
        for (idx, row), group in zip(unmatched_rows, remaining_groups):
            updated[idx] = _matched_row_payload(updated[idx], group)
            matched += 1
            before_paying, before_exempt = _city_tax_counts_from_row(row)
            after_paying = int(group.get("paying_guests") or 0)
            after_exempt = int(group.get("exempt_guests") or 0)
            overwritten_fields = {}
            if before_paying != after_paying:
                overwritten_fields["city_tax_paying_guests"] = {"old": before_paying, "new": after_paying}
            if before_exempt != after_exempt:
                overwritten_fields["city_tax_exempt_guests"] = {"old": before_exempt, "new": after_exempt}
            detail = {
                "reason": "Deterministic fallback match in equal-size date bucket.",
                "checkin_reservation_id": group["reservation_id"],
                "checkin_source_file_id": group.get("source_file_id"),
                "checkin_property_name": group["property_name"],
                "checkin_total_guests": group["total_guests"],
                "checkin_missing_age_guests": group["missing_age_guests"],
                "after_paying_guests": after_paying,
                "after_exempt_guests": after_exempt,
            }
            audit_records.append(
                _reservation_audit_record(
                    row,
                    match_status="MATCHED",
                    overwritten_fields=overwritten_fields,
                    detail=detail,
                )
            )
            group_status[group["reservation_id"]] = {
                "status": "MATCHED",
                "detail": {
                    "reason": "Equal-size fallback match.",
                    "matched_confirmation_code": row.get("confirmation_code", ""),
                    "hostify_reservation_id": row.get("reservation_id", ""),
                },
            }

        for group in group_items:
            status = group_status.get(group["reservation_id"], {"status": "UNMATCHED_GROUP", "detail": {}})
            audit_records.append(
                _group_audit_record(
                    group,
                    match_status=status["status"],
                    detail=status["detail"],
                )
            )

    seen_reservation_keys = {
        (
            record.get("confirmation_code", ""),
            record.get("check_in", ""),
            record.get("check_out", ""),
            record.get("guest_name", ""),
            record.get("record_type", ""),
        )
        for record in audit_records
        if record.get("record_type") == "reservation"
    }
    for row in updated:
        key = (
            row.get("confirmation_code", ""),
            row.get("check_in", ""),
            row.get("check_out", ""),
            row.get("guest_name", ""),
            "reservation",
        )
        if key in seen_reservation_keys:
            continue
        reservations_without_evidence += 1
        audit_records.append(
            _reservation_audit_record(
                row,
                match_status="NO_EVIDENCE",
                detail={"reason": "Reservation was not matched by any evidence hostů group."},
            )
        )

    matched_group_ids = {
        record.get("checkin_reservation_id", "")
        for record in audit_records
        if record.get("record_type") == "evidence_group" and record.get("match_status") == "MATCHED"
    }
    unmatched_groups = len(
        [group for group in relevant_groups if group["reservation_id"] not in matched_group_ids]
    )

    return updated, {
        "matched": matched,
        "ambiguous_buckets": ambiguous_buckets,
        "unmatched_groups": unmatched_groups,
        "reservations_without_evidence": reservations_without_evidence,
        "audit_records": audit_records,
    }
