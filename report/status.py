"""
report/status.py — shared presentation/business status helpers.
"""

from __future__ import annotations

from collections import Counter


def effective_verification_status(row: dict) -> tuple[str, str]:
    status = str(row.get("verification_status") or "").strip()
    if status != "MATCHED":
        return status, ""
    if not row.get("tax_verification_required"):
        return status, ""
    missing_age_guests = int(row.get("checkin_missing_age_guests") or 0)
    if missing_age_guests > 0:
        return "KE KONTROLE", "Checkin: chybí věk hostů pro místní poplatky."
    if not row.get("checkin_verified"):
        return "KE KONTROLE", "Checkin: místní poplatky nejsou ověřeny."
    return status, ""


def count_effective_verification_statuses(rows: list[dict]) -> Counter:
    counts: Counter = Counter()
    for row in rows:
        status, _note = effective_verification_status(row)
        counts[status] += 1
    return counts
