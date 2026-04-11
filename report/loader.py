"""
report/loader.py — Fetch and normalize reservations from Hostify API.

Key architecture notes:
- Hostify has master listings (e.g. id=184988) and channel sub-listings (e.g. id=206426).
  Reservations are stored under sub-listings, but share the same `listing_nickname`.
- To get all reservations for a property, fetch by date range and filter by listing_nickname.
- The `listing_id` simple param does NOT work as a filter in the Hostify API.
  Use `filters` JSON array format for date range queries.
"""

import json
import logging
import sys
from calendar import monthrange
from datetime import date

import hostify_api as h

logger = logging.getLogger(__name__)

# Hostify cache TTL in hours
_HOSTIFY_CACHE_TTL_HOURS_CURRENT = 2       # current / future window — data may still change
_HOSTIFY_CACHE_TTL_HOURS_PAST = 24 * 30   # completed past window — data is immutable


class HostifyApiError(Exception):
    pass


class HostifyFetchIncompleteError(Exception):
    """Raised when Hostify API returns fewer reservations than the reported total."""
    def __init__(self, fetched: int, total: int, from_date: str, to_date: str):
        self.fetched = fetched
        self.total = total
        self.from_date = from_date
        self.to_date = to_date
        super().__init__(
            f"Hostify fetch incomplete: loaded {fetched} of {total} reservations "
            f"for window {from_date} to {to_date}. "
            "Report generation stopped — critical source data is incomplete."
        )


# --------------------------------------------------------------------------- #
#  Month assignment logic                                                      #
# --------------------------------------------------------------------------- #

def assign_report_month(checkin: date, checkout: date, nights: int, source: str) -> tuple[int, int]:
    """
    Determine which (year, month) a reservation belongs to based on business rules.

    Airbnb:
      - Always check-in month.

    Booking:
      - Default: check-in month.
      - Exception: if check-out is in a different month AND checkout.day > 5 → check-OUT month.

    Returns (year, month).
    """
    src = (source or "").lower()

    if "airbnb" in src:
        return checkin.year, checkin.month

    if "booking" in src:
        if checkout.month != checkin.month and checkout.day > 5:
            return checkout.year, checkout.month
        return checkin.year, checkin.month

    # Default: check-in month for unknown sources
    return checkin.year, checkin.month


def _month_assignment_comment(
    checkin: date, checkout: date, nights: int, source: str,
    assigned_year: int, assigned_month: int
) -> str | None:
    """
    Returns a warning comment if:
    - the reservation was assigned to a non-obvious month, OR
    - it's a long cross-month stay that needs manual review.
    Returns None if assignment is straightforward.
    """
    if assigned_year != checkin.year or assigned_month != checkin.month:
        return (
            f"⚠ PŘIŘAZENO JINÝ MĚSÍC: check-in {checkin.isoformat()}, "
            f"check-out {checkout.isoformat()}, nights={nights}, "
            f"source={source} → assigned {assigned_year}-{assigned_month:02d}"
        )
    # Flag long stays that span multiple months for manual review
    if checkout.month != checkin.month and nights > 20:
        return (
            f"⚠ DLOUHÝ POBYT: {nights} nocí, "
            f"check-in {checkin.isoformat()}, check-out {checkout.isoformat()} "
            f"— zkontrolovat přiřazení měsíce"
        )
    return None


# --------------------------------------------------------------------------- #
#  Hostify API fetching                                                        #
# --------------------------------------------------------------------------- #

def _fetch_page(filters_json: str, page: int, limit: int = 100) -> dict:
    """Fetch one page of reservations with given filters."""
    try:
        return h.hostify_get(
            "reservations",
            params={"filters": filters_json, "limit": limit, "page": page}
        )
    except Exception as e:
        raise HostifyApiError(f"Hostify API error on page {page}: {e}") from e


def _make_cache_key(from_date: str, to_date: str) -> str:
    return f"reservations:{from_date}:{to_date}"


def fetch_raw_reservations_for_period(
    year: int,
    month: int,
    extra_months: int = 1,
    limit: int = 100,
    *,
    use_cache: bool = True,
    db_conn=None,
) -> list[dict]:
    """
    Fetch all Hostify reservations for a date window around the target month.

    Cache strategy:
      1. Check SQLite cache (if use_cache=True)
      2. Fetch from Hostify API if cache miss or expired
      3. Save to SQLite cache for next run

    Returns list of raw Hostify reservation dicts.
    """
    _, last_day = monthrange(year, month)
    from_date = _shift_month(year, month, -extra_months)
    to_date = _shift_month(year, month, extra_months, last_day=True)
    cache_key = _make_cache_key(from_date, to_date)

    # --- Try SQLite cache ---
    if use_cache:
        try:
            from report.db import get_connection, get_hostify_cache, save_hostify_cache
            conn = db_conn or get_connection()
            cached = get_hostify_cache(conn, cache_key)
            if cached is not None:
                logger.info(
                    "Hostify cache HIT: %d reservations for %s to %s",
                    len(cached), from_date, to_date
                )
                if db_conn is None:
                    conn.close()
                return cached
            if db_conn is None:
                conn.close()
        except Exception as e:
            logger.warning("SQLite cache read failed: %s", e)

    # --- Fetch from API ---
    filters_json = json.dumps([
        {"field": "checkIn", "operator": ">=", "value": from_date},
        {"field": "checkIn", "operator": "<=", "value": to_date},
    ])

    all_reservations: list[dict] = []
    page = 1
    # Hostify ignores the limit param and returns ~20 items/page regardless.
    # 500 pages × 20 = 10 000 reservations — well above any realistic portfolio size.
    max_pages = 500
    total = 0

    while page <= max_pages:
        result = _fetch_page(filters_json, page, limit)
        batch = result.get("reservations") or []
        all_reservations.extend(batch)
        total = result.get("total", 0)
        logger.debug(
            "Fetched page %d: %d reservations (total=%d)", page, len(batch), total
        )
        if len(all_reservations) >= total or not batch:
            break
        page += 1

    if len(all_reservations) < total:
        raise HostifyFetchIncompleteError(
            fetched=len(all_reservations),
            total=total,
            from_date=from_date,
            to_date=to_date,
        )

    logger.info(
        "Loaded %d raw reservations from Hostify API for window %s to %s",
        len(all_reservations), from_date, to_date,
    )

    # --- Save to SQLite cache (only on complete fetch) ---
    if use_cache:
        try:
            from report.db import get_connection, save_hostify_cache
            conn = db_conn or get_connection()
            # Use the target month (not the window end date) to decide TTL.
            # The window extends into the following month (extra_months=1), so comparing
            # to_date with today would wrongly treat past-month reports as "current".
            today = date.today()
            is_past_month = (year, month) < (today.year, today.month)
            ttl = _HOSTIFY_CACHE_TTL_HOURS_PAST if is_past_month else _HOSTIFY_CACHE_TTL_HOURS_CURRENT
            save_hostify_cache(conn, cache_key, all_reservations, ttl_hours=ttl)
            if db_conn is None:
                conn.close()
            logger.debug("Hostify API response cached (TTL=%dh)", ttl)
        except Exception as e:
            logger.warning("SQLite cache write failed: %s", e)

    return all_reservations


def _shift_month(year: int, month: int, delta: int, last_day: bool = False) -> str:
    """Shift a month by `delta` months and return 'YYYY-MM-DD' string."""
    total_months = year * 12 + (month - 1) + delta
    y = total_months // 12
    m = total_months % 12 + 1
    if last_day:
        _, d = monthrange(y, m)
    else:
        d = 1
    return f"{y}-{m:02d}-{d:02d}"


# --------------------------------------------------------------------------- #
#  Normalizing & filtering                                                     #
# --------------------------------------------------------------------------- #

SKIP_STATUSES = {
    "inquiry",
    "expired",
    "declined",
    "request",
    "timedout",
    "voided",
    "not_possible",
    "denied",
    "preapproved",
}


def _normalize_reservation(raw: dict) -> dict | None:
    """
    Normalize a raw Hostify reservation dict into a consistent HostifyReservation dict.
    Returns None if the reservation should be skipped entirely
    (non-final/non-stayed statuses like inquiry/timedout/voided).
    """
    status = (raw.get("status") or "").lower()
    if status in SKIP_STATUSES:
        return None

    payout_price = float(raw.get("payout_price") or raw.get("payout_price_eur") or 0)
    is_cancelled = status == "cancelled"

    # Skip cancelled with zero payout — nothing to report
    if is_cancelled and payout_price <= 0:
        return None

    checkin_str = raw.get("checkIn") or raw.get("check_in") or ""
    checkout_str = raw.get("checkOut") or raw.get("check_out") or ""
    if not checkin_str or not checkout_str:
        logger.warning(
            "Reservation id=%s has no dates, skipping.",
            raw.get("id") or raw.get("reservation_id"),
        )
        return None

    try:
        checkin = date.fromisoformat(checkin_str)
        checkout = date.fromisoformat(checkout_str)
    except ValueError:
        logger.warning("Bad dates in reservation id=%s: %s %s", raw.get("id"), checkin_str, checkout_str)
        return None

    nights = int(raw.get("nights") or 0) or max((checkout - checkin).days, 1)
    source = raw.get("source") or "Unknown"

    # CNB rate date: use confirmed_at, fall back to created_at, then check-in
    confirmed_at = raw.get("confirmed_at") or raw.get("created_at") or checkin_str
    if raw.get("confirmed_at") is None and raw.get("created_at"):
        logger.debug(
            "Reservation id=%s has no confirmed_at, using created_at=%s",
            raw.get("id"), raw.get("created_at")
        )

    assigned_year, assigned_month = assign_report_month(checkin, checkout, nights, source)
    month_comment = _month_assignment_comment(
        checkin, checkout, nights, source, assigned_year, assigned_month
    )

    return {
        "reservation_id": str(raw.get("id") or raw.get("reservation_id") or ""),
        "confirmation_code": str(raw.get("channel_reservation_id") or raw.get("confirmation_code") or ""),
        "guest_name": raw.get("guest_name") or "",
        "check_in": checkin_str,
        "check_out": checkout_str,
        "nights": nights,
        "adults": int(raw.get("adults") or 0),
        "children": int(raw.get("children") or 0),
        "infants": int(raw.get("infants") or 0),
        "cleaning_fee_eur": float(raw.get("cleaning_fee") or raw.get("cleaning_fee_eur") or 0),
        "city_tax_eur": float(raw.get("city_tax") or raw.get("city_tax_eur") or 0),
        "channel_commission_eur": float(raw.get("channel_commission") or raw.get("channel_commission_eur") or 0) + float(raw.get("transaction_fee") or 0),
        "payout_price_eur": payout_price,
        "source": source,
        "status": status,
        "is_cancelled": is_cancelled,
        "confirmed_at": str(confirmed_at),
        "listing_id": raw.get("listing_id"),
        "listing_nickname": raw.get("listing_nickname") or "",
        "assigned_year": assigned_year,
        "assigned_month": assigned_month,
        "month_comment": month_comment,  # None or warning string
    }


def normalize_reservation(raw: dict) -> dict | None:
    """Public wrapper used by other modules to persist normalized Hostify rows."""
    return _normalize_reservation(raw)


def normalize_reservations(all_reservations: list[dict]) -> list[dict]:
    """Normalize a raw Hostify payload list and skip ignorable rows."""
    result = []
    for raw in all_reservations:
        normalized = _normalize_reservation(raw)
        if normalized is not None:
            result.append(normalized)
    return result


def filter_for_property_month(
    all_reservations: list[dict],
    listing_nickname: str,
    year: int,
    month: int,
    extra_nicknames: list[str] | None = None,
) -> list[dict]:
    """
    From a pre-fetched list of raw Hostify reservations, return only those
    belonging to the given property (by listing_nickname) and target month.
    `extra_nicknames` allows matching additional channel sub-listings
    (e.g. "28. Pluku 58 - Bcom" for Booking.com).
    """
    nicknames = {listing_nickname}
    if extra_nicknames:
        nicknames.update(extra_nicknames)
    result = []
    for raw in all_reservations:
        if raw.get("listing_nickname") not in nicknames:
            continue
        normalized = _normalize_reservation(raw)
        if normalized is None:
            continue
        if normalized["assigned_year"] == year and normalized["assigned_month"] == month:
            result.append(normalized)

    # Deduplicate: if both an inquiry/accepted exist for same confirmation_code, keep accepted
    seen: dict[str, dict] = {}
    for r in result:
        code = r["confirmation_code"]
        if code not in seen:
            seen[code] = r
        else:
            # Prefer accepted over others
            existing = seen[code]
            if r["status"] == "accepted" and existing["status"] != "accepted":
                seen[code] = r

    deduped = list(seen.values())
    deduped.sort(key=lambda r: r["check_in"])
    return deduped


# --------------------------------------------------------------------------- #
#  High-level entry points                                                     #
# --------------------------------------------------------------------------- #

def fetch_reservations(
    listing_nickname: str,
    year: int,
    month: int,
    *,
    all_raw: list[dict] | None = None,
) -> list[dict]:
    """
    Return normalized reservations for the given listing_nickname and month.

    If `all_raw` is provided (pre-fetched), uses that instead of calling the API.
    This allows main.py to fetch once and pass the same data to all properties.
    """
    if all_raw is None:
        all_raw = fetch_raw_reservations_for_period(year, month)
    return filter_for_property_month(all_raw, listing_nickname, year, month)


def fetch_all_properties_for_month(
    config: dict,
    year: int,
    month: int,
) -> dict[str, list[dict]]:
    """
    Fetch reservations for ALL configured properties for the given month.
    Makes one API call set and distributes results to each property.

    Returns {slug: [HostifyReservation, ...]}
    """
    # Single fetch for all properties
    all_raw = fetch_raw_reservations_for_period(year, month)

    results: dict[str, list[dict]] = {}
    for slug, prop in config["properties"].items():
        nickname = prop.get("listing_nickname", "")
        try:
            reservations = filter_for_property_month(all_raw, nickname, year, month)
            results[slug] = reservations
            logger.info(
                "Property %s (%s): %d reservations for %d-%02d",
                slug, nickname, len(reservations), year, month
            )
        except Exception as e:
            logger.error("Error processing property %s: %s", slug, e)
            results[slug] = []

    return results
