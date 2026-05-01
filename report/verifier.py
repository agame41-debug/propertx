"""
report/verifier.py — Load Airbnb/Booking CSV exports and cross-verify against Hostify data.

Key matching logic:
- Airbnb: match by confirmation_code (Hostify) ↔ "Potvrzující kód" (CSV)
- Booking: match by confirmation_code (Hostify) ↔ "Referenční číslo" (CSV)

Verification statuses:
  MATCHED        — payout matches within tolerance (±1.00 EUR)
  ROZDÍL         — pair found but amount differs; use CSV value
  CHYBÍ_V_CSV    — reservation in Hostify but not in CSV
  CHYBÍ_V_HOSTIFY — reservation in CSV but not in Hostify
  ZRUŠENO        — cancelled reservation (payout_price > 0)
"""

import csv
import io
import logging
import os
from datetime import date

logger = logging.getLogger(__name__)

TOLERANCE_EUR = 1.00

# Sanity bounds for Airbnb-batch implied rate (CZK per EUR). CNB historical
# range is roughly 23–27; values outside [15, 35] indicate that the batch's
# eur_sum was polluted by an item whose "Částka" came in CZK rather than EUR
# (or other CSV corruption). We refuse to trust such rates and let the
# calculator fall back to CNB rate by reservation date.
_AIRBNB_RATE_MIN = 15.0
_AIRBNB_RATE_MAX = 35.0

# Required columns for each CSV type — used to detect format changes early.
_AIRBNB_REQUIRED_COLS = {
    "Typ", "Potvrzující kód", "Datum rezervace", "Datum zahájení",
    "Datum ukončení", "Počet nocí", "Host", "Nabídka",
    "Částka", "Hrubé výdělky", "Servisní poplatek", "Poplatek za úklid",
}
_BOOKING_REQUIRED_COLS = {
    "Typ / typ transakce", "Referenční číslo", "Datum příjezdu", "Datum odjezdu",
    "Název ubytování", "ID ubytování", "Datum vyplacení částky",
    "Hrubá částka", "Provize", "Hodnota transakce", "Směnný kurz", "Splatná částka",
    "Poplatek za\xa0platební služby",
}


class CsvFormatError(ValueError):
    """Raised when an input CSV no longer matches the expected schema."""


def _check_required_columns(rows: list[dict], required: set[str], label: str, kind: str) -> None:
    """Fail fast when expected columns are missing from a CSV file."""
    if not rows:
        return
    actual = set(rows[0].keys())
    missing = required - actual
    if missing:
        raise CsvFormatError(
            f"{kind} CSV {label} is missing expected columns: {sorted(missing)}. "
            "The file format may have changed."
        )


# Verification status constants
STATUS_MATCHED = "MATCHED"
STATUS_ROZDIL = "ROZDÍL"
STATUS_CHYBI_CSV = "CHYBÍ_V_CSV"
STATUS_CHYBI_HOSTIFY = "CHYBÍ_V_HOSTIFY"
STATUS_ZRUSENO = "ZRUŠENO"
STATUS_KE_KONTROLE = "KE KONTROLE"


# --------------------------------------------------------------------------- #
#  CSV loading                                                                 #
# --------------------------------------------------------------------------- #

def _safe_float(v) -> float:
    """Convert a string to float, handling European comma separators."""
    try:
        return float(str(v).replace(",", ".").replace("\xa0", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_date(s: str) -> date | None:
    """Try common date formats."""
    for fmt in ("%m/%d/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _source_name(source) -> str:
    if isinstance(source, str):
        return os.path.basename(source)
    return str(source.get("original_name") or source.get("name") or f"db:{source.get('id', '?')}")


def _source_bytes(source) -> bytes:
    if isinstance(source, str):
        with open(source, "rb") as f:
            return f.read()
    content = source.get("content") or b""
    if isinstance(content, memoryview):
        content = content.tobytes()
    return bytes(content)


def _decode_csv_text(raw: bytes, encodings: list[str]) -> str:
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return raw.decode("utf-8", errors="replace")


def _detect_csv_delimiter(text: str) -> str:
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    if not first_line:
        return ","
    candidates = [",", ";", "\t", "|"]
    counts = {delimiter: first_line.count(delimiter) for delimiter in candidates}
    best = max(candidates, key=lambda delimiter: counts[delimiter])
    return best if counts[best] > 0 else ","


def _read_csv_rows(csv_sources: list, encodings: list[str], kind: str) -> list[tuple[str, list[dict]]]:
    """Load CSV rows from file paths or DB-backed source blobs."""
    result: list[tuple[str, list[dict]]] = []
    for source in csv_sources:
        label = _source_name(source)
        if isinstance(source, str) and not os.path.exists(source):
            logger.warning("%s CSV not found: %s", kind, source)
            continue
        try:
            raw = _source_bytes(source)
            text = _decode_csv_text(raw, encodings)
            delimiter = _detect_csv_delimiter(text)
            result.append((label, list(csv.DictReader(io.StringIO(text), delimiter=delimiter))))
        except Exception as e:
            logger.error("Error reading %s CSV %s: %s", kind, label, e)
    return result


def load_airbnb_csv(csv_paths: list) -> dict[str, dict]:
    """
    Parse Airbnb CSV export files.
    Returns dict keyed by confirmation_code (Potvrzující kód).
    Keeps only "Rezervace" rows (skips "Payout" and others).
    Aggregates split payouts for the same reservation code while avoiding
    double-counting identical rows from overlapping exports.
    """
    index: dict[str, dict] = {}
    seen_signatures_by_code: dict[str, set[tuple]] = {}
    encodings = ["utf-8-sig", "cp1250", "latin1"]  # Airbnb exports vary by locale/export mode

    for label, rows in _read_csv_rows(csv_paths, encodings, "Airbnb"):
        _check_required_columns(rows, _AIRBNB_REQUIRED_COLS, label, "Airbnb")
        for row in rows:
            typ = (row.get("Typ") or "").strip()
            if typ != "Rezervace":
                continue
            code = (row.get("Potvrzující kód") or "").strip()
            if not code:
                continue

            amount_eur = _safe_float(row.get("Částka", 0))
            gross_eur = _safe_float(row.get("Hrubé výdělky", 0))
            service_fee_eur = _safe_float(row.get("Servisní poplatek", 0))
            cleaning_fee_eur = _safe_float(row.get("Poplatek za úklid", 0))
            row_signature = (
                (row.get("Datum") or "").strip(),
                (row.get("Datum připsání na účet") or "").strip(),
                (row.get("Referenční kód") or "").strip(),
                amount_eur,
                gross_eur,
                service_fee_eur,
                cleaning_fee_eur,
            )
            code_signatures = seen_signatures_by_code.setdefault(code, set())
            if row_signature in code_signatures:
                continue
            code_signatures.add(row_signature)

            if code not in index:
                index[code] = {
                    "confirmation_code": code,
                    "date_reserved": _parse_date(row.get("Datum rezervace", "")),
                    "check_in": _parse_date(row.get("Datum zahájení", "")),
                    "check_out": _parse_date(row.get("Datum ukončení", "")),
                    "nights": int(_safe_float(row.get("Počet nocí", 0))),
                    "guest": (row.get("Host") or "").strip(),
                    "listing": (row.get("Nabídka") or "").strip(),
                    "amount_eur": amount_eur,
                    "gross_eur": gross_eur,
                    "service_fee_eur": service_fee_eur,
                    "cleaning_fee_eur": cleaning_fee_eur,
                    "source_file": label,
                }
                continue

            index[code]["amount_eur"] += amount_eur
            index[code]["gross_eur"] += gross_eur
            index[code]["service_fee_eur"] += service_fee_eur
            index[code]["cleaning_fee_eur"] += cleaning_fee_eur
            if not index[code].get("guest"):
                index[code]["guest"] = (row.get("Host") or "").strip()
            if not index[code].get("listing"):
                index[code]["listing"] = (row.get("Nabídka") or "").strip()
            if not index[code].get("source_file"):
                index[code]["source_file"] = label

    logger.info("Loaded %d Airbnb reservations from %d files", len(index), len(csv_paths))
    return index


_HOSTIFY_BOOKING_CITY_TAX_EUR_PER_PERSON_NIGHT = 2.0

def _infer_booking_city_tax_eur(reservation: dict, csv_row: dict | None, property_config: dict | None) -> float:
    """Infer the city-tax amount that Hostify adds to Booking payout_price_eur.

    Hostify uses a flat 2 EUR per person per night for ALL guests
    (adults + children + infants) regardless of the property's CZK rate.
    """
    if "booking" not in str(reservation.get("source") or "").lower():
        return 0.0

    nights = int(reservation.get("nights") or 0)
    if nights <= 0:
        return 0.0

    adults = int(reservation.get("adults") or 0)
    children = int(reservation.get("children") or 0)
    infants = int(reservation.get("infants") or 0)
    total_guests = adults + children + infants
    if total_guests <= 0:
        return 0.0

    return round(_HOSTIFY_BOOKING_CITY_TAX_EUR_PER_PERSON_NIGHT * total_guests * nights, 2)


def _booking_city_tax_matches_diff(diff: float, inferred_city_tax_eur: float, tolerance_eur: float) -> bool:
    if diff <= 0 or inferred_city_tax_eur <= 0:
        return False
    city_tax_tolerance = max(float(tolerance_eur), 0.25)
    return abs(diff - inferred_city_tax_eur) <= city_tax_tolerance


def _effective_booking_city_tax_eur(
    reservation: dict,
    csv_row: dict | None,
    property_config: dict | None,
) -> tuple[float, float]:
    """
    Resolve the city-tax amount that should be removed from Booking Hostify payout
    before comparing against Booking CSV net payout.

    Hostify often reports city_tax_eur=0 for Booking but still includes the
    city tax in payout_price_eur.  When Hostify's own value is zero, fall back
    to the inferred amount (nights × guests × rate / booking_rate) so that
    the verification comparison removes the tax and avoids a false ROZDÍL.

    Returns:
      (effective_city_tax_eur, inferred_city_tax_eur)
    """
    hostify_city_tax = float(reservation.get("city_tax_eur", 0.0) or 0.0)
    inferred_city_tax_eur = 0.0
    if csv_row and property_config:
        inferred_city_tax_eur = _infer_booking_city_tax_eur(reservation, csv_row, property_config)
    effective = hostify_city_tax if hostify_city_tax > 0 else inferred_city_tax_eur
    return effective, inferred_city_tax_eur


def verify_reservation(
    reservation: dict,
    airbnb_index: dict[str, dict],
    booking_index: dict[str, dict],
    tolerance_eur: float = TOLERANCE_EUR,
    *,
    property_config: dict | None = None,
) -> dict:
    """
    Cross-verify a Hostify reservation against CSV data.

    Returns a VerificationResult dict merged into the reservation.
    The `effective_payout_eur` field contains the value that calculator.py should use.
    """
    code = reservation.get("confirmation_code", "")
    source = (reservation.get("source") or "").lower()
    hostify_payout = float(reservation.get("payout_price_eur", 0.0) or 0.0)
    comparable_hostify_payout = hostify_payout
    effective_booking_city_tax_eur = 0.0
    inferred_booking_city_tax_eur = 0.0

    # AirCover items — separate compensation, excluded from calculation
    if reservation.get("is_aircover"):
        return {
            **reservation,
            "verification_status": STATUS_KE_KONTROLE,
            "effective_payout_eur": float(reservation.get("effective_payout_eur") or hostify_payout),
            "csv_payout_eur": None,
            "verification_diff": None,
            "csv_source_file": None,
            "verification_comment": reservation.get("aircover_details") or "AirCover – kompenzace od Airbnb",
        }

    # Payout adjustments use their own amount, not CSV totals
    if reservation.get("is_payout_adjustment"):
        return {
            **reservation,
            "verification_status": "ADJUSTMENT",
            "effective_payout_eur": float(reservation.get("effective_payout_eur") or hostify_payout),
            "csv_payout_eur": None,
            "verification_diff": None,
            "csv_source_file": None,
            "verification_comment": (
                f"Doplatek z předchozího měsíce "
                f"({reservation.get('adjustment_original_month')}/{reservation.get('adjustment_original_year')})"
            ),
        }

    # Cancelled reservations get their own status
    if reservation.get("is_cancelled"):
        return {
            **reservation,
            "verification_status": STATUS_ZRUSENO,
            "effective_payout_eur": comparable_hostify_payout,
            "csv_payout_eur": None,
            "verification_diff": None,
            "csv_source_file": None,
            "verification_comment": f"ZRUŠENO: payout={comparable_hostify_payout} EUR",
        }

    csv_row = None
    csv_payout = None

    if "airbnb" in source:
        csv_row = airbnb_index.get(code)
        if csv_row:
            csv_payout = csv_row["amount_eur"]
    elif "booking" in source:
        csv_row = booking_index.get(code)
        if csv_row:
            csv_payout = csv_row["net_eur"]

    # Extra fields from Booking CSV for bank matching
    booking_czk = csv_row.get("czk_booked") if csv_row else None
    booking_payout_date = csv_row.get("payout_date", "") if csv_row else ""
    booking_rate = csv_row.get("booking_rate") if csv_row else None
    # For Booking: total commission = Provize + Poplatek za platební služby
    booking_total_commission = csv_row.get("total_commission_eur") if csv_row else None

    if "booking" in source:
        effective_booking_city_tax_eur, inferred_booking_city_tax_eur = _effective_booking_city_tax_eur(
            reservation,
            csv_row,
            property_config,
        )
        if effective_booking_city_tax_eur > 0:
            comparable_hostify_payout = max(hostify_payout - effective_booking_city_tax_eur, 0.0)

    if csv_row is None:
        # Not found in any CSV
        return {
            **reservation,
            "verification_status": STATUS_CHYBI_CSV,
            "effective_payout_eur": comparable_hostify_payout,
            "csv_payout_eur": None,
            "verification_diff": None,
            "csv_source_file": None,
            "czk_booked": None,
            "booking_rate": None,
            "booking_payout_date": "",
            "verification_comment": f"Není v CSV reportu ({source}). Použita hodnota Hostify.",
        }

    diff = round(comparable_hostify_payout - csv_payout, 4)
    csv_source_file = csv_row.get("source_file", "")

    # Override channel_commission_eur for Booking with total (Provize + Poplatek)
    commission_override = {}
    if "booking" in source and booking_total_commission is not None:
        commission_override = {"channel_commission_eur": booking_total_commission}

    if abs(diff) <= tolerance_eur:
        return {
            **reservation,
            **commission_override,
            "verification_status": STATUS_MATCHED,
            "effective_payout_eur": csv_payout if "booking" in source else comparable_hostify_payout,
            "csv_payout_eur": csv_payout,
            "verification_diff": diff,
            "csv_source_file": csv_source_file,
            "czk_booked": booking_czk,
            "booking_rate": booking_rate,
            "booking_payout_date": booking_payout_date,
            "inferred_city_tax_eur": inferred_booking_city_tax_eur or None,
            "verification_comment": "",
        }
    else:
        return {
            **reservation,
            **commission_override,
            "verification_status": STATUS_ROZDIL,
            "effective_payout_eur": csv_payout,   # USE CSV value
            "csv_payout_eur": csv_payout,
            "verification_diff": diff,
            "csv_source_file": csv_source_file,
            "czk_booked": booking_czk,
            "booking_rate": booking_rate,
            "booking_payout_date": booking_payout_date,
            "inferred_city_tax_eur": inferred_booking_city_tax_eur or None,
            "verification_comment": (
                f"⚠ ROZDÍL: Hostify={hostify_payout}, CSV={csv_payout}, "
                + (
                    f"city_tax={effective_booking_city_tax_eur}, net={comparable_hostify_payout}, "
                    if "booking" in source and effective_booking_city_tax_eur > 0
                    else ""
                )
                + f"diff={diff:+.2f} EUR. Použita hodnota z CSV."
            ),
        }


def build_airbnb_payout_data(csv_paths: list) -> dict:
    """
    Parse Airbnb payout exports into:
      - reservation_map: confirmation_code -> batch info
      - batches: normalized batch headers
      - items: per-batch breakdown rows for UI drill-down
    """
    import re as _re

    reservation_map: dict[str, dict] = {}
    all_batches_map: dict[str, list[dict]] = {}  # code → [batch_info, ...]
    aircover_map: dict[str, list[dict]] = {}  # code → [aircover_item, ...]
    batches_out: list[dict] = []
    items_out: list[dict] = []
    seen_batches: set[str] = set()
    encodings = ["utf-8-sig", "cp1250", "latin1"]

    for label, rows in _read_csv_rows(csv_paths, encodings, "Airbnb"):
        _check_required_columns(rows, _AIRBNB_REQUIRED_COLS, label, "Airbnb")
        batches: dict[str, dict] = {}
        cur_gref = ""

        for row in rows:
            typ = (row.get("Typ") or "").strip()
            if typ == "Payout":
                ref = (row.get("Referenční kód") or "").strip()
                m = _re.search(r"G-[A-Z0-9]+", ref.upper())
                cur_gref = m.group(0) if m else ref.strip().upper()
                if not cur_gref:
                    continue
                batches[cur_gref] = {
                    "batch_ref": cur_gref,
                    "batch_match_ref": cur_gref,
                    "payout_date": (row.get("Datum") or "").strip(),
                    "credited_date": (row.get("Datum připsání na účet") or "").strip(),
                    "amount_czk": _safe_float(row.get("Vyplaceno", 0)),
                    "amount_eur": 0.0,
                    "implied_rate": 0.0,
                    "rate_anomaly": False,
                    "source_name": label,
                    "_eur_contribs": [],
                }
            elif cur_gref in batches:
                eur_amt = _safe_float(row.get("Částka", 0))
                batches[cur_gref]["amount_eur"] += eur_amt
                batches[cur_gref]["_eur_contribs"].append({
                    "amount_eur": eur_amt,
                    "confirmation_code": (row.get("Potvrzující kód") or "").strip(),
                    "guest_name": (row.get("Host") or "").strip(),
                    "item_type": typ,
                })

        for batch in batches.values():
            eur_sum = batch["amount_eur"]
            raw_rate = (batch["amount_czk"] / eur_sum) if eur_sum > 0 else 0.0
            if raw_rate > 0 and not (_AIRBNB_RATE_MIN <= raw_rate <= _AIRBNB_RATE_MAX):
                # Anomalous rate — most likely caused by a single item whose
                # "Částka" was recorded in CZK instead of EUR. Refuse the rate
                # so the calculator falls back to CNB by reservation date,
                # then log the top contributors so the operator can spot the
                # bad CSV row.
                top = sorted(
                    batch.get("_eur_contribs", []),
                    key=lambda it: abs(it.get("amount_eur") or 0.0),
                    reverse=True,
                )[:3]
                top_summary = ", ".join(
                    f"{it['confirmation_code'] or '?'}/{(it['guest_name'] or '?')[:20]}={it['amount_eur']:.2f}"
                    for it in top
                )
                logger.warning(
                    "Airbnb batch %s rate anomaly: implied_rate=%.4f CZK/EUR is outside [%.1f, %.1f] "
                    "(amount_czk=%.2f, eur_sum=%.2f). Falling back to CNB rate per reservation. "
                    "Top contributors: %s",
                    batch["batch_ref"], raw_rate, _AIRBNB_RATE_MIN, _AIRBNB_RATE_MAX,
                    batch["amount_czk"], eur_sum, top_summary or "(none)",
                )
                batch["implied_rate"] = 0.0
                batch["rate_anomaly"] = True
            else:
                batch["implied_rate"] = round(raw_rate, 6)
            batch.pop("_eur_contribs", None)

        cur_gref = ""
        accepted_batches: set[str] = set()
        item_index_by_batch: dict[str, int] = {}
        for row in rows:
            typ = (row.get("Typ") or "").strip()
            if typ == "Payout":
                ref = (row.get("Referenční kód") or "").strip()
                m = _re.search(r"G-[A-Z0-9]+", ref.upper())
                cur_gref = m.group(0) if m else ref.strip().upper()
                if cur_gref and cur_gref not in seen_batches and cur_gref in batches:
                    batches_out.append(batches[cur_gref])
                    seen_batches.add(cur_gref)
                    accepted_batches.add(cur_gref)
                continue
            if not cur_gref or cur_gref not in batches or cur_gref not in accepted_batches:
                continue

            item_index_by_batch[cur_gref] = item_index_by_batch.get(cur_gref, 0) + 1
            eur_amount = _safe_float(row.get("Částka", 0))
            batch = batches[cur_gref]
            code = (row.get("Potvrzující kód") or "").strip()
            items_out.append({
                "batch_ref": cur_gref,
                "item_index": item_index_by_batch[cur_gref],
                "item_type": typ,
                "confirmation_code": code,
                "guest_name": (row.get("Host") or "").strip(),
                "listing_name": (row.get("Nabídka") or "").strip(),
                "property_id": "",
                "amount_eur": eur_amount,
                "amount_czk": round(eur_amount * batch["implied_rate"], 2) if batch["implied_rate"] else None,
                "check_in": _parse_date(row.get("Datum zahájení", "")).isoformat() if _parse_date(row.get("Datum zahájení", "")) else "",
                "check_out": _parse_date(row.get("Datum ukončení", "")).isoformat() if _parse_date(row.get("Datum ukončení", "")) else "",
                "source_name": label,
            })
            if typ == "Rezervace" and code:
                new_entry = {
                    "batch_ref": cur_gref,
                    "gref": cur_gref,
                    "payout_date": batch["payout_date"],
                    "payout_czk": batch["amount_czk"],
                    "airbnb_rate": batch["implied_rate"],
                    "credited_date": batch["credited_date"],
                    "payout_eur": abs(eur_amount),
                    "_res_eur": abs(eur_amount),
                }
                all_batches_map.setdefault(code, []).append(new_entry)
                if code not in reservation_map or abs(eur_amount) > reservation_map[code].get("_res_eur", 0):
                    reservation_map[code] = new_entry
            elif typ != "Rezervace" and code and "řešení" in typ.lower():
                if "výplata" in typ.lower():
                    # "Výplata jako výsledek řešení" → AirCover (Airbnb
                    # compensates host for damages).  Separate excluded row.
                    aircover_map.setdefault(code, []).append({
                        "batch_ref": cur_gref,
                        "gref": cur_gref,
                        "payout_date": batch["payout_date"],
                        "airbnb_rate": batch["implied_rate"],
                        "amount_eur": eur_amount,
                        "amount_czk": round(eur_amount * batch["implied_rate"], 2) if batch["implied_rate"] else None,
                        "batch_czk": batch["amount_czk"],
                        "guest_name": (row.get("Host") or "").strip(),
                        "listing_name": (row.get("Nabídka") or "").strip(),
                        "check_in": _parse_date(row.get("Datum zahájení", "")).isoformat() if _parse_date(row.get("Datum zahájení", "")) else "",
                        "check_out": _parse_date(row.get("Datum ukončení", "")).isoformat() if _parse_date(row.get("Datum ukončení", "")) else "",
                        "nights": int(_safe_float(row.get("Počet nocí", 0))),
                        "item_type": typ,
                        "details": (row.get("Podrobnosti") or "").strip(),
                    })
                else:
                    # "Vyrovnání z řešení" → payout adjustment (money
                    # returned to guest).  Treated as __ADJ row.
                    adj_entry = {
                        "batch_ref": cur_gref,
                        "gref": cur_gref,
                        "payout_date": batch["payout_date"],
                        "payout_czk": batch["amount_czk"],
                        "airbnb_rate": batch["implied_rate"],
                        "credited_date": batch["credited_date"],
                        "payout_eur": eur_amount,
                        "_res_eur": abs(eur_amount),
                    }
                    all_batches_map.setdefault(code, []).append(adj_entry)

    if aircover_map:
        logger.info("AirCover items: %d codes, %d items total",
                     len(aircover_map), sum(len(v) for v in aircover_map.values()))

    return {
        "reservation_map": reservation_map,
        "all_batches_map": all_batches_map,
        "aircover_map": aircover_map,
        "batches": batches_out,
        "items": items_out,
    }


def build_payout_ref_map(csv_paths: list) -> dict[str, dict]:
    """
    Parse Airbnb CSV and map each reservation to its payout batch.

    Two-pass per file:
      Pass 1 — for each Payout batch: collect CZK amount and sum all EUR from
               Rezervace rows → compute implied Airbnb exchange rate.
      Pass 2 — assign each Rezervace its batch gref + computed rate.

    Returns dict:
        {confirmation_code: {
            "gref":         "G-XXXXX",
            "payout_date":  "MM/DD/YYYY",
            "payout_czk":   float,   # full batch CZK (all properties)
            "airbnb_rate":  float,   # batch_czk / sum_eur (Airbnb's implied rate)
        }}
    """
    result = build_airbnb_payout_data(csv_paths)["reservation_map"]
    logger.info("Payout ref map: %d reservation codes mapped to G-refs", len(result))
    return result


def load_booking_csv(csv_paths: list) -> dict[str, dict]:
    """
    Parse Booking.com Payout CSV export files.
    Returns dict keyed by reference_number (Referenční číslo).
    Keeps only "Rezervace" rows.
    """
    index: dict[str, dict] = {}
    encodings = ["utf-8-sig", "cp1250", "latin1"]

    for label, rows in _read_csv_rows(csv_paths, encodings, "Booking"):
        _check_required_columns(rows, _BOOKING_REQUIRED_COLS, label, "Booking")
        for row in rows:
            typ = (row.get("Typ / typ transakce") or "").strip()
            if typ == "(Payout)":
                continue
            ref = (row.get("Referenční číslo") or "").strip()
            if not ref or ref in index:
                continue
            gross = _safe_float(row.get("Hrubá částka", 0))
            commission = _safe_float(row.get("Provize", 0))
            # "Poplatek za platební služby" uses non-breaking space (\xa0) in the CSV header
            payment_fee = _safe_float(row.get("Poplatek za\xa0platební služby", 0))
            # Total commission = Booking commission + payment service fee (both are costs)
            total_commission = abs(commission) + abs(payment_fee)
            net_eur = _safe_float(row.get("Hodnota transakce", 0))
            booking_rate = _safe_float(row.get("Směnný kurz", 0))
            czk = _safe_float(row.get("Splatná částka", 0))
            index[ref] = {
                "reference_number": ref,
                "check_in": _parse_date(row.get("Datum příjezdu", "")),
                "check_out": _parse_date(row.get("Datum odjezdu", "")),
                "property_name": (row.get("Název ubytování") or "").strip(),
                "property_id": (row.get("ID ubytování") or "").strip(),
                "payout_date": (row.get("Datum vyplacení částky") or "").strip(),
                "gross_eur": gross,
                "commission_eur": commission,
                "payment_fee_eur": payment_fee,
                "total_commission_eur": total_commission,
                "net_eur": net_eur,
                "booking_rate": booking_rate,
                "czk_booked": czk,
                "source_file": label,
            }

    logger.info("Loaded %d Booking reservations from %d files", len(index), len(csv_paths))
    return index


def build_booking_payout_data(csv_paths: list) -> dict:
    """
    Parse Booking payout exports into batch headers, items and reservation->batch map.
    """
    reservation_map: dict[str, dict] = {}
    batches_out: list[dict] = []
    items_out: list[dict] = []
    seen_batches: set[str] = set()
    encodings = ["utf-8-sig", "cp1250", "latin1"]

    for label, rows in _read_csv_rows(csv_paths, encodings, "Booking"):
        _check_required_columns(rows, _BOOKING_REQUIRED_COLS, label, "Booking")
        batches: dict[str, dict] = {}
        cur_ref = ""

        for row in rows:
            typ = (row.get("Typ / typ transakce") or "").strip()
            ref = (row.get("Deskriptor výpisu") or "").strip()
            if typ == "(Payout)":
                cur_ref = ref
                if not cur_ref:
                    continue
                batches[cur_ref] = {
                    "batch_ref": cur_ref,
                    "batch_match_ref": cur_ref,
                    "payout_date": (row.get("Datum vyplacení částky") or "").strip(),
                    "credited_date": (row.get("Datum vyplacení částky") or "").strip(),
                    "amount_czk": _safe_float(row.get("Vyplacená částka", 0)),
                    "amount_eur": 0.0,
                    "implied_rate": 0.0,
                    "source_name": label,
                }
            elif cur_ref in batches and typ == "Rezervace":
                batches[cur_ref]["amount_eur"] += _safe_float(row.get("Hodnota transakce", 0))

        for batch in batches.values():
            eur_sum = batch["amount_eur"]
            batch["implied_rate"] = round(batch["amount_czk"] / eur_sum, 6) if eur_sum > 0 else 0.0

        cur_ref = ""
        accepted_batches: set[str] = set()
        item_index_by_batch: dict[str, int] = {}
        for row in rows:
            typ = (row.get("Typ / typ transakce") or "").strip()
            ref = (row.get("Deskriptor výpisu") or "").strip()
            if typ == "(Payout)":
                cur_ref = ref
                if cur_ref and cur_ref not in seen_batches and cur_ref in batches:
                    batches_out.append(batches[cur_ref])
                    seen_batches.add(cur_ref)
                    accepted_batches.add(cur_ref)
                continue
            if not cur_ref or cur_ref not in batches or cur_ref not in accepted_batches:
                continue

            item_index_by_batch[cur_ref] = item_index_by_batch.get(cur_ref, 0) + 1
            code = (row.get("Referenční číslo") or "").strip()
            item_type = typ or "Unknown"
            amount_eur = _safe_float(row.get("Hodnota transakce", 0))
            amount_czk = _safe_float(row.get("Splatná částka", 0))
            check_in = _parse_date(row.get("Datum příjezdu", ""))
            check_out = _parse_date(row.get("Datum odjezdu", ""))
            items_out.append({
                "batch_ref": cur_ref,
                "item_index": item_index_by_batch[cur_ref],
                "item_type": item_type,
                "confirmation_code": code,
                "guest_name": "",
                "listing_name": (row.get("Název ubytování") or "").strip(),
                "property_id": (row.get("ID ubytování") or "").strip(),
                "amount_eur": amount_eur,
                "amount_czk": amount_czk or (round(amount_eur * batches[cur_ref]["implied_rate"], 2) if batches[cur_ref]["implied_rate"] else None),
                "check_in": check_in.isoformat() if check_in else "",
                "check_out": check_out.isoformat() if check_out else "",
                "source_name": label,
            })
            if item_type == "Rezervace" and code and code not in reservation_map:
                batch = batches[cur_ref]
                # item amounts: what Booking actually transferred for this
                # specific reservation (after Booking commission). For
                # cancelled-but-paid bookings these are the authoritative
                # numbers — the Hostify gross is misleading, and load_booking_csv
                # does not return cancelled rows at all.
                fallback_item_czk = (
                    round(amount_eur * batch["implied_rate"], 2)
                    if batch["implied_rate"] else None
                )
                reservation_map[code] = {
                    "batch_ref": cur_ref,
                    "payout_date": batch["payout_date"],
                    "payout_czk": batch["amount_czk"],
                    "booking_batch_rate": batch["implied_rate"],
                    "property_id": (row.get("ID ubytování") or "").strip(),
                    "item_amount_eur": amount_eur,
                    "item_amount_czk": amount_czk or fallback_item_czk,
                }

    return {
        "reservation_map": reservation_map,
        "batches": batches_out,
        "items": items_out,
    }


def find_csv_only_rows(
    reservations: list[dict],
    airbnb_index: dict[str, dict],
    booking_index: dict[str, dict],
    property_config: dict,
    year: int = 0,
    month: int = 0,
    hidden_confirmation_codes: set[str] | None = None,
) -> list[dict]:
    """
    Find CSV rows that have NO matching Hostify reservation for this property.

    Only looks at CSV rows where the listing name matches the property's listing_nickname
    AND the check-in falls within the target year/month (if provided).
    Returns list of partial reservation dicts with status=CHYBÍ_V_HOSTIFY.
    """
    matched_codes = {r.get("confirmation_code", "") for r in reservations}
    hidden_codes = {
        str(code).strip()
        for code in (hidden_confirmation_codes or set())
        if str(code).strip()
    }
    from report.config import get_hostify_listing_names
    hostify_listing_names = get_hostify_listing_names(property_config, year=year or None, month=month or None)
    nickname = hostify_listing_names[0] if hostify_listing_names else property_config.get("listing_nickname", "")
    results = []

    # Airbnb CSV — match by listing name (supports channel aliases from config)
    from report.config import get_airbnb_listing_names
    airbnb_listing_names = set(get_airbnb_listing_names(property_config, year=year or None, month=month or None))

    for code, row in airbnb_index.items():
        if code in matched_codes or code in hidden_codes:
            continue
        if row.get("listing", "").strip() not in airbnb_listing_names:
            continue
        check_in_d = row.get("check_in")
        check_out_d = row.get("check_out")
        nights = row.get("nights") or ((check_out_d - check_in_d).days if check_out_d and check_in_d else 0)
        # Use the same centralized month assignment logic as Hostify reservations
        if year and month and check_in_d:
            from report.loader import assign_report_month
            ay, am = assign_report_month(check_in_d, check_out_d or check_in_d, nights, "Airbnb")
            if ay != year or am != month:
                continue
        else:
            ay = check_in_d.year if check_in_d else 0
            am = check_in_d.month if check_in_d else 0
        results.append({
            "reservation_id": "",
            "confirmation_code": code,
            "guest_name": row.get("guest", ""),
            "check_in": check_in_d.isoformat() if check_in_d else "",
            "check_out": check_out_d.isoformat() if check_out_d else "",
            "nights": nights,
            "adults": 0,
            "children": 0,
            "infants": 0,
            "children_infants": 0,
            "cleaning_fee_eur": row.get("cleaning_fee_eur", 0.0),
            "channel_commission_eur": row.get("service_fee_eur", 0.0),
            "payout_price_eur": row.get("amount_eur", 0.0),
            "source": "Airbnb",
            "status": "unknown",
            "is_cancelled": False,
            "confirmed_at": row["date_reserved"].isoformat() if row.get("date_reserved") else "",
            "listing_id": None,
            "listing_nickname": nickname,
            "assigned_year": ay,
            "assigned_month": am,
            "month_comment": None,
            "verification_status": STATUS_CHYBI_HOSTIFY,
            "effective_payout_eur": row.get("amount_eur", 0.0),
            "csv_payout_eur": row.get("amount_eur", 0.0),
            "verification_diff": None,
            "csv_source_file": row.get("source_file", ""),
            "verification_comment": "⚠ V Hostify nenalezeno. Data z CSV.",
        })

    # Booking CSV — match by booking_property_id (preferred) or property name (fallback)
    from report.config import get_booking_config
    booking_pid = get_booking_config(property_config, year=year or None, month=month or None).get("property_id", "")
    for ref, row in booking_index.items():
        if ref in matched_codes or ref in hidden_codes:
            continue
        if booking_pid:
            if row.get("property_id", "").strip() != booking_pid:
                continue
        else:
            if row.get("property_name", "").strip() != nickname:
                continue
        check_in_d = row.get("check_in")
        check_out_d = row.get("check_out")
        # Filter by month if provided (apply Booking month assignment rule)
        if year and month and check_in_d:
            nights_approx = (check_out_d - check_in_d).days if check_out_d else 0
            from report.loader import assign_report_month
            ay, am = assign_report_month(check_in_d, check_out_d or check_in_d, nights_approx, "Booking.com")
            if ay != year or am != month:
                continue
        results.append({
            "reservation_id": "",
            "confirmation_code": ref,
            "guest_name": row.get("guest_name", ""),
            "check_in": check_in_d.isoformat() if check_in_d else "",
            "check_out": check_out_d.isoformat() if check_out_d else "",
            "nights": 0,
            "adults": 0,
            "children": 0,
            "infants": 0,
            "children_infants": 0,
            "cleaning_fee_eur": 0.0,
            "channel_commission_eur": row.get("total_commission_eur") or abs(row.get("commission_eur", 0.0)),
            "payout_price_eur": row.get("net_eur", 0.0),
            "source": "Booking.com",
            "status": "unknown",
            "is_cancelled": False,
            "confirmed_at": check_in_d.isoformat() if check_in_d else "",
            "listing_id": None,
            "listing_nickname": nickname,
            "assigned_year": check_in_d.year if check_in_d else 0,
            "assigned_month": check_in_d.month if check_in_d else 0,
            "month_comment": None,
            "czk_booked": row.get("czk_booked"),
            "booking_rate": row.get("booking_rate"),
            "booking_payout_date": row.get("payout_date", ""),
            "verification_status": STATUS_CHYBI_HOSTIFY,
            "effective_payout_eur": row.get("net_eur", 0.0),
            "csv_payout_eur": row.get("net_eur", 0.0),
            "verification_diff": None,
            "csv_source_file": row.get("source_file", ""),
            "verification_comment": "⚠ V Hostify nenalezeno. Data z CSV.",
        })

    return results


def build_verification_index(
    reservations: list[dict],
    airbnb_index: dict[str, dict],
    booking_index: dict[str, dict],
    property_config: dict,
    hostify_lookup: dict[str, dict] | None = None,
    tolerance_eur: float = TOLERANCE_EUR,
    year: int = 0,
    month: int = 0,
    hidden_confirmation_codes: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Run full verification for all reservations.

    Returns:
      (verified_reservations, csv_only_rows)
      - verified_reservations: each Hostify reservation with verification fields merged in
      - csv_only_rows: CSV rows with no Hostify match (CHYBÍ_V_HOSTIFY)
    """
    verified = [
        verify_reservation(
            r,
            airbnb_index,
            booking_index,
            tolerance_eur,
            property_config=property_config,
        )
        for r in reservations
    ]
    csv_only_raw = find_csv_only_rows(
        reservations,
        airbnb_index,
        booking_index,
        property_config,
        year=year,
        month=month,
        hidden_confirmation_codes=hidden_confirmation_codes,
    )

    hostify_lookup = hostify_lookup or {}
    late_linked: list[dict] = []
    csv_only: list[dict] = []
    for row in csv_only_raw:
        code = row.get("confirmation_code", "")
        source = (row.get("source") or "").lower()
        linked = hostify_lookup.get(code) if "booking" in source else None
        if linked:
            verified_row = verify_reservation(
                linked,
                airbnb_index,
                booking_index,
                tolerance_eur,
                property_config=property_config,
            )
            existing_comment = verified_row.get("verification_comment", "")
            late_note = "Hostify reservation linked later by booking id."
            verified_row["verification_comment"] = (
                f"{existing_comment} | {late_note}" if existing_comment else late_note
            )
            late_linked.append(verified_row)
        else:
            csv_only.append(row)

    verified.extend(late_linked)

    # Log summary
    from collections import Counter
    counts = Counter(r["verification_status"] for r in verified)
    logger.info(
        "Verification: %s | CSV-only rows: %d | late Hostify links: %d",
        dict(counts), len(csv_only), len(late_linked)
    )
    return verified, csv_only
