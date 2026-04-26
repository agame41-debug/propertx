"""
report/calculator.py — Pure financial calculation engine.

No I/O, no API calls. All inputs and outputs are plain dicts with JSON-serializable types.
This module is the single source of truth for all financial formulas.

Formula reference:
  city_tax          = city_tax_rate × nights × adults
  provize_czk       = channel_commission_eur × provize_kurz
  dph_provize       = provize_czk × vat_rate
  payout_czk        = actual paid CZK (Booking: Splatná částka; Airbnb: EUR payout × Airbnb batch rate)
  uklid_czk         = cleaning_fee_eur × kurz
  balicky           = balicky_per_person × city_tax_guest_count
  dph_uklid_balicky = (uklid_czk + balicky) × vat_rate
  priprava_pokoje   = uklid_czk + balicky
  cena_ubytovani    = payout_czk − priprava_pokoje − city_tax − dph_provize − dph_uklid_balicky
"""

import logging
from datetime import date

log = logging.getLogger(__name__)


def _r(v) -> float:
    """Round to 2 decimal places."""
    return round(float(v), 2)


def _stay_label(check_in: str, check_out: str) -> str:
    """
    Format stay dates as "05.12.-07.12." matching the example Excel format.
    Input: "YYYY-MM-DD" strings.
    """
    try:
        ci = date.fromisoformat(check_in)
        co = date.fromisoformat(check_out)
        return f"{ci.day:02d}.{ci.month:02d}.-{co.day:02d}.{co.month:02d}."
    except (ValueError, TypeError):
        return f"{check_in} → {check_out}"


def calculate_row(
    reservation: dict,
    cnb_rate_result: dict,
    property_config: dict,
    order: int,
) -> dict:
    """
    Calculate all financial fields for one reservation.

    Args:
        reservation: verified HostifyReservation dict (with verification fields merged in)
        cnb_rate_result: {"rate": float, "valid_for": str, ...} from cnb.py
        property_config: single property config dict
        order: 1-based row number

    Returns:
        CalculatedRow dict with all computed fields.
        Fields that cannot be computed (e.g. CHYBÍ_V_HOSTIFY rows with missing data) are None.
    """
    # Airbnb: implied batch rate from Airbnb payout export for payout/cleaning.
    #         Commission uses CNB rate on reservation date.
    # Booking: actual payout rate from Booking payout export / booked CZK.
    # Other: CNB rate on confirmed_at date.
    airbnb_batch_rate = float(reservation.get("airbnb_batch_rate") or 0)
    booking_rate = float(reservation.get("booking_rate") or 0)
    source = (reservation.get("source") or "").lower()
    payout_eur = reservation.get("effective_payout_eur") or 0.0
    czk_booked = reservation.get("czk_booked") or 0.0
    # Refund-flow rows can have payout_eur < 0 with a positive czk_booked
    # snapshot (or vice versa). Sign-mismatched division yields a negative
    # implied rate that would propagate everywhere downstream — clamp here.
    derived_booking_rate = 0.0
    if "booking" in source and czk_booked and payout_eur:
        candidate = float(czk_booked) / float(payout_eur)
        if candidate > 0:
            derived_booking_rate = candidate

    if "airbnb" in source and airbnb_batch_rate > 0:
        kurz = airbnb_batch_rate
        kurz_date = reservation.get("airbnb_payout_date", "")
    elif "booking" in source and (derived_booking_rate > 0 or booking_rate > 0):
        kurz = derived_booking_rate if derived_booking_rate > 0 else booking_rate
        kurz_date = reservation.get("booking_payout_date", "") or reservation.get("batch_payout_date", "")
    else:
        kurz = float(cnb_rate_result.get("rate", 0))
        kurz_date = str(cnb_rate_result.get("valid_for", ""))

    provize_kurz = kurz
    if "airbnb" in source:
        provize_kurz = float(cnb_rate_result.get("rate", 0))

    city_tax_rate = float(property_config.get("city_tax_rate", 50))
    balicky_per_person = float(property_config.get("balicky_per_person", 0))
    vat_rate = float(property_config.get("vat_rate", 0.21))

    nights = reservation.get("nights") or 0
    occupancy_adults = reservation.get("occupancy_adults")
    if occupancy_adults is None:
        occupancy_adults = reservation.get("adults") or 0
    occupancy_children = reservation.get("occupancy_children")
    if occupancy_children is None:
        occupancy_children = reservation.get("children") or 0
    occupancy_infants = reservation.get("occupancy_infants")
    if occupancy_infants is None:
        occupancy_infants = reservation.get("infants") or 0
    occupancy_children_infants = occupancy_children + occupancy_infants

    city_tax_paying_guests = reservation.get("city_tax_paying_guests")
    if city_tax_paying_guests is None:
        city_tax_paying_guests = occupancy_adults
    city_tax_exempt_guests = reservation.get("city_tax_exempt_guests")
    if city_tax_exempt_guests is None:
        city_tax_exempt_guests = occupancy_children_infants
    city_tax_guest_count = city_tax_paying_guests + city_tax_exempt_guests

    cleaning_eur = reservation.get("cleaning_fee_eur") or 0.0
    commission_eur = reservation.get("channel_commission_eur") or 0.0

    check_in = reservation.get("check_in", "")
    check_out = reservation.get("check_out", "")

    # Build comment: combine month assignment warning + verification comment
    comments = []
    if reservation.get("month_comment"):
        comments.append(reservation["month_comment"])
    if reservation.get("verification_comment"):
        comments.append(reservation["verification_comment"])
    comment = " | ".join(c for c in comments if c)

    # If no exchange rate, we can't compute CZK fields
    if kurz <= 0:
        return _null_row(reservation, order, comment)

    is_cancelled = bool(reservation.get("is_cancelled"))
    is_payout_adjustment = bool(reservation.get("is_payout_adjustment"))
    is_split_transaction = bool(reservation.get("is_split_transaction"))

    # Core calculations — carry full float precision, round at assignment
    is_aircover = bool(reservation.get("is_aircover"))
    _no_fees = is_cancelled or is_payout_adjustment or is_split_transaction or is_aircover
    city_tax = 0.0 if _no_fees else (city_tax_rate * nights * city_tax_paying_guests)
    provize_czk = commission_eur * provize_kurz
    dph_provize = provize_czk * vat_rate
    payout_czk = float(czk_booked) if ("booking" in source and czk_booked) else (payout_eur * kurz)
    uklid_czk = 0.0 if _no_fees else (cleaning_eur * kurz)
    balicky = 0.0 if _no_fees else (balicky_per_person * city_tax_guest_count)
    dph_uklid_balicky = (uklid_czk + balicky) * vat_rate
    priprava_pokoje = uklid_czk + balicky
    raw_cena_ubytovani = (
        payout_czk - priprava_pokoje - city_tax - dph_provize - dph_uklid_balicky
    )
    if raw_cena_ubytovani < 0:
        log.warning(
            "cena_ubytovani < 0 clamped to 0: code=%s nights=%s payout_czk=%.2f "
            "priprava_pokoje=%.2f city_tax=%.2f dph_provize=%.2f dph_uklid_balicky=%.2f",
            reservation.get("confirmation_code", ""),
            nights,
            payout_czk,
            priprava_pokoje,
            city_tax,
            dph_provize,
            dph_uklid_balicky,
        )
    cena_ubytovani = max(raw_cena_ubytovani, 0.0)

    return {
        # Identity
        "order": order,
        "guest_name": reservation.get("guest_name", ""),
        "stay_label": _stay_label(check_in, check_out),
        "check_in": check_in,
        "check_out": check_out,
        "nights": nights,
        "adults": city_tax_paying_guests,
        "children_infants": city_tax_exempt_guests,
        "occupancy_adults": occupancy_adults,
        "occupancy_children_infants": occupancy_children_infants,
        "source": reservation.get("source", ""),
        "confirmation_code": reservation.get("confirmation_code", ""),
        "listing_id": reservation.get("listing_id"),
        "listing_nickname": reservation.get("listing_nickname", ""),
        "tax_verification_required": bool(reservation.get("tax_verification_required")),
        "checkin_verified": bool(reservation.get("checkin_verified")),
        "checkin_reservation_id": reservation.get("checkin_reservation_id", ""),
        "checkin_total_guests": reservation.get("checkin_total_guests"),
        "checkin_missing_age_guests": reservation.get("checkin_missing_age_guests", 0),
        "checkin_property_name": reservation.get("checkin_property_name", ""),
        "batch_ref": reservation.get("batch_ref", ""),
        "batch_payout_date": reservation.get("batch_payout_date", ""),
        "batch_amount_czk_expected": reservation.get("batch_amount_czk"),
        "batch_rate": reservation.get("batch_rate"),

        # EUR inputs
        "payout_eur": _r(payout_eur),
        "cleaning_fee_eur": _r(cleaning_eur),
        "channel_commission_eur": _r(commission_eur),

        # CNB rate
        "kurz": kurz,
        "kurz_date": kurz_date,

        # Calculated CZK fields (columns G–Q in Excel)
        "city_tax_czk": _r(city_tax),           # G: Místní poplatky
        "provize_czk": _r(provize_czk),          # H: Provize platformy
        "dph_provize_czk": _r(dph_provize),      # I: DPH z provize
        "payout_czk": _r(payout_czk),            # K: Cena host Euro (komplet)
        "uklid_czk": _r(uklid_czk),              # M: Úklid
        "balicky_czk": _r(balicky),              # N: Balíčky
        "dph_uklid_balicky_czk": _r(dph_uklid_balicky),  # O: DPH z (úklid+Balíčky)
        "priprava_pokoje_czk": _r(priprava_pokoje),       # P: Příprava pokoje bez DPH
        "cena_ubytovani_czk": _r(cena_ubytovani),         # Q: Cena ubytování

        # Verification
        "verification_status": reservation.get("verification_status", ""),
        "verification_diff": reservation.get("verification_diff"),
        "csv_payout_eur": reservation.get("csv_payout_eur"),
        "comment": comment,

        # Booking bank matching helpers (None for Airbnb rows)
        "czk_booked": reservation.get("czk_booked"),
        "booking_rate": reservation.get("booking_rate"),
        "booking_payout_date": reservation.get("booking_payout_date", ""),

        # Reservation controls flags
        "is_payout_adjustment": is_payout_adjustment,
        "is_excluded": bool(reservation.get("is_excluded")),
        "is_aircover": bool(reservation.get("is_aircover")),
        "aircover_details": reservation.get("aircover_details", ""),
        "aircover_parent_code": reservation.get("aircover_parent_code", ""),
        "adjustment_original_year": reservation.get("adjustment_original_year"),
        "adjustment_original_month": reservation.get("adjustment_original_month"),
        "adjustment_parent_code": reservation.get("adjustment_parent_code", ""),
        "is_split_transaction": is_split_transaction,
        "split_parent_code": reservation.get("split_parent_code", ""),
    }


def _null_row(reservation: dict, order: int, comment: str) -> dict:
    """Return a row with None for all computed fields (used when kurz=0)."""
    return {
        "order": order,
        "guest_name": reservation.get("guest_name", ""),
        "stay_label": _stay_label(
            reservation.get("check_in", ""), reservation.get("check_out", "")
        ),
        "check_in": reservation.get("check_in", ""),
        "check_out": reservation.get("check_out", ""),
        "nights": reservation.get("nights"),
        "adults": reservation.get("city_tax_paying_guests", reservation.get("adults")),
        "children_infants": reservation.get(
            "city_tax_exempt_guests",
            ((reservation.get("children") or 0) + (reservation.get("infants") or 0)),
        ),
        "occupancy_adults": reservation.get("occupancy_adults", reservation.get("adults")),
        "occupancy_children_infants": (
            (reservation.get("occupancy_children") if reservation.get("occupancy_children") is not None else reservation.get("children") or 0)
            + (reservation.get("occupancy_infants") if reservation.get("occupancy_infants") is not None else reservation.get("infants") or 0)
        ),
        "source": reservation.get("source", ""),
        "confirmation_code": reservation.get("confirmation_code", ""),
        "listing_id": reservation.get("listing_id"),
        "listing_nickname": reservation.get("listing_nickname", ""),
        "tax_verification_required": bool(reservation.get("tax_verification_required")),
        "checkin_verified": bool(reservation.get("checkin_verified")),
        "checkin_reservation_id": reservation.get("checkin_reservation_id", ""),
        "checkin_total_guests": reservation.get("checkin_total_guests"),
        "checkin_missing_age_guests": reservation.get("checkin_missing_age_guests", 0),
        "checkin_property_name": reservation.get("checkin_property_name", ""),
        "batch_ref": reservation.get("batch_ref", ""),
        "batch_payout_date": reservation.get("batch_payout_date", ""),
        "batch_amount_czk_expected": reservation.get("batch_amount_czk"),
        "batch_rate": reservation.get("batch_rate"),
        "payout_eur": reservation.get("effective_payout_eur"),
        "cleaning_fee_eur": reservation.get("cleaning_fee_eur"),
        "channel_commission_eur": reservation.get("channel_commission_eur"),
        "kurz": None,
        "kurz_date": None,
        "city_tax_czk": None,
        "provize_czk": None,
        "dph_provize_czk": None,
        "payout_czk": None,
        "uklid_czk": None,
        "balicky_czk": None,
        "dph_uklid_balicky_czk": None,
        "priprava_pokoje_czk": None,
        "cena_ubytovani_czk": None,
        "verification_status": reservation.get("verification_status", ""),
        "verification_diff": reservation.get("verification_diff"),
        "csv_payout_eur": reservation.get("csv_payout_eur"),
        "comment": comment + " | ⚠ CNB kurz nedostupný",

        # Booking bank matching helpers
        "czk_booked": reservation.get("czk_booked"),
        "booking_rate": reservation.get("booking_rate"),
        "booking_payout_date": reservation.get("booking_payout_date", ""),

        # Reservation controls flags
        "is_payout_adjustment": bool(reservation.get("is_payout_adjustment")),
        "is_excluded": bool(reservation.get("is_excluded")),
        "is_aircover": bool(reservation.get("is_aircover")),
        "aircover_details": reservation.get("aircover_details", ""),
        "aircover_parent_code": reservation.get("aircover_parent_code", ""),
        "adjustment_original_year": reservation.get("adjustment_original_year"),
        "adjustment_original_month": reservation.get("adjustment_original_month"),
        "adjustment_parent_code": reservation.get("adjustment_parent_code", ""),
        "is_split_transaction": bool(reservation.get("is_split_transaction")),
        "split_parent_code": reservation.get("split_parent_code", ""),
    }


def calculate_all_rows(
    reservations: list[dict],
    cnb_rates: dict[str, dict],
    property_config: dict,
) -> list[dict]:
    """
    Calculate all rows for a property-month report.

    Args:
        reservations: verified HostifyReservation dicts (already sorted by check_in from loader)
        cnb_rates: {confirmed_at_date_str: CnbRateResult} - pre-fetched rates
        property_config: single property config dict

    Returns:
        List of CalculatedRow dicts, sorted by check_in, numbered 1-based.
    """
    # Sort by check_in
    sorted_res = sorted(reservations, key=lambda r: r.get("check_in", ""))

    rows = []
    for i, res in enumerate(sorted_res, start=1):
        confirmed_at = res.get("confirmed_at", "") or res.get("check_in", "")
        rate_date = confirmed_at[:10] if confirmed_at else ""
        cnb_rate = cnb_rates.get(rate_date, {"rate": 0, "valid_for": ""})
        row = calculate_row(res, cnb_rate, property_config, order=i)
        rows.append(row)

    return rows


def calculate_totals(rows: list[dict]) -> dict:
    """
    Compute column sums for all numeric CZK/EUR fields.
    Also computes Rentero commission and related totals.
    """
    numeric_fields = [
        "nights", "adults", "children_infants",
        "payout_eur", "cleaning_fee_eur", "channel_commission_eur",
        "city_tax_czk", "provize_czk", "dph_provize_czk",
        "payout_czk", "uklid_czk", "balicky_czk",
        "dph_uklid_balicky_czk", "priprava_pokoje_czk", "cena_ubytovani_czk",
    ]

    totals: dict = {}
    for field in numeric_fields:
        total = sum(
            float(r[field]) for r in rows
            if r.get(field) is not None
        )
        totals[field] = _r(total)

    # Rentero management fee
    rentero_commission = 0.15  # default; caller should pass via property_config
    if rows:
        # Try to get from the rows' implied config — for now use default
        pass

    totals["rentero_odmena"] = _r(totals.get("cena_ubytovani_czk", 0) * rentero_commission)
    totals["dph_rentero_odmena"] = _r(totals["rentero_odmena"] * 0.21)
    totals["priprava_pokoje_s_dph"] = _r(
        totals.get("priprava_pokoje_czk", 0) +
        totals.get("dph_uklid_balicky_czk", 0)
    )
    # What client (property owner) receives
    totals["klient_prijem_brutto"] = _r(totals.get("cena_ubytovani_czk", 0))
    totals["klient_vyplaceno"] = _r(
        totals["klient_prijem_brutto"]
        - totals["rentero_odmena"]
        - totals["dph_rentero_odmena"]
    )

    return totals


def calculate_totals_with_config(rows: list[dict], property_config: dict) -> dict:
    """Like calculate_totals but uses property_config for commission rate."""
    totals = calculate_totals(rows)
    rentero_commission = float(property_config.get("rentero_commission", 0.15))
    vat_rate = float(property_config.get("vat_rate", 0.21))

    cena_ubytovani = totals.get("cena_ubytovani_czk", 0)
    totals["rentero_odmena"] = _r(cena_ubytovani * rentero_commission)
    totals["dph_rentero_odmena"] = _r(totals["rentero_odmena"] * vat_rate)
    totals["klient_prijem_brutto"] = _r(cena_ubytovani)
    totals["klient_vyplaceno"] = _r(
        cena_ubytovani - totals["rentero_odmena"] - totals["dph_rentero_odmena"]
    )
    # DPH přefakturace klient
    totals["dph_prefakturace_klient"] = _r(
        totals.get("dph_uklid_balicky_czk", 0)
        + totals["dph_rentero_odmena"]
    )
    return totals
