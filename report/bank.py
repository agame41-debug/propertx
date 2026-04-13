"""
report/bank.py — Load bank CSV and match payouts to bank transactions.

Airbnb payouts: CITIBANK EUROPE PLC, G-XXXXXXX reference in "Zpráva pro příjemce".
  - Strict G-ref only match. No amount/date fallback.

Booking.com payouts: BOOKING.COM B.V., message format "NO.XXXXX/{property_id}".
  - Strict descriptor reference match: normalize "Deskriptor výpisu" from payout CSV
    and compare with normalized bank "Zpráva pro příjemce". No amount/date fallback.

Public API:
    load_bank_csv(paths)                              -> list[dict]    (Airbnb/CITIBANK only)
    build_bank_index(rows)                            -> (dict[gref, row], list[row])
    match_bank_transaction(...)                       -> dict | None
    match_booking_by_ref(...)                         -> dict | None
    enrich_rows_with_bank(...)                        -> list[dict]
    load_booking_bank_transactions(paths)             -> dict[str, list[dict]]
    enrich_booking_rows_with_bank(rows, idx, config)  -> list[dict]
"""

import csv
import io
import logging
import os
import re
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)



# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _safe_float(v) -> float:
    try:
        return float(str(v).replace("\xa0", "").replace(",", ".").replace(" ", "").strip())
    except (ValueError, TypeError):
        return 0.0


def _parse_date(s: str) -> date | None:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _extract_gref(text: str) -> str:
    """Extract G-XXXXXXX from a bank message field. Returns '' if not found."""
    m = re.search(r"G-[A-Z0-9]+", (text or "").upper())
    return m.group(0) if m else ""


def _source_name(source) -> str:
    if isinstance(source, str):
        return os.path.basename(source)
    return str(source.get("original_name") or f"db:{source.get('id', '?')}")


def _source_text(source, encoding: str):
    if isinstance(source, str):
        return open(source, encoding=encoding)
    content = source.get("content") or b""
    if isinstance(content, memoryview):
        content = content.tobytes()
    return io.TextIOWrapper(io.BytesIO(bytes(content)), encoding=encoding)


def _bank_tx_key(row: dict) -> str:
    """Stable transaction key even when bank export has no tx_id."""
    tx_id = (row.get("tx_id") or "").strip()
    if tx_id:
        return tx_id
    datum = row.get("datum")
    datum_str = datum.isoformat() if hasattr(datum, "isoformat") else str(datum or "")
    return "|".join([
        datum_str,
        f"{float(row.get('amount_czk') or 0):.2f}",
        row.get("gref", "") or row.get("property_id", ""),
        row.get("zprava", "")[:120],
    ])


# --------------------------------------------------------------------------- #
#  Loading                                                                     #
# --------------------------------------------------------------------------- #

def filter_bank_by_cutoff(rows: list[dict], cutoff_date) -> list[dict]:
    """Return only bank rows with datum <= cutoff_date. None datum rows are excluded."""
    return [r for r in rows if r.get("datum") and r["datum"] <= cutoff_date]


def load_bank_csv(paths: list) -> list[dict]:
    """
    Load bank CSV (UTF-16, comma-separated).
    Keeps only incoming Airbnb transfers: 'Příchozí úhrada' from CITIBANK EUROPE.
    Returns list of normalised bank row dicts.
    """
    rows: list[dict] = []

    for path in paths:
        label = _source_name(path)
        if isinstance(path, str) and not os.path.exists(path):
            logger.warning("Bank CSV not found: %s", path)
            continue
        try:
            with _source_text(path, "utf-16") as f:
                for row in csv.DictReader(f):
                    if row.get("Typ transakce", "").strip() != "Příchozí úhrada":
                        continue
                    protiucet = row.get("Název protiúčtu", "").upper()
                    if "CITIBANK EUROPE" not in protiucet:
                        continue
                    amount = _safe_float(row.get("Částka", 0))
                    if amount <= 0:
                        continue
                    zprava = row.get("Zpráva pro příjemce", "").strip()
                    ref_platby = row.get("Reference platby", "").strip()
                    gref = _extract_gref(zprava) or _extract_gref(ref_platby)
                    datum = _parse_date(row.get("Datum zaúčtování", "").strip())
                    rows.append({
                        "datum":      datum,
                        "amount_czk": amount,
                        "gref":       gref,
                        "tx_id":      row.get("ID transakce", "").strip(),
                        "zprava":     zprava,
                        "source_name": label,
                    })
        except Exception as e:
            logger.error("Error reading bank CSV %s: %s", label, e)

    for row in rows:
        row["tx_key"] = _bank_tx_key(row)

    logger.info("Loaded %d Airbnb bank transactions from %d file(s)", len(rows), len(paths))
    return rows


# --------------------------------------------------------------------------- #
#  Indexing                                                                    #
# --------------------------------------------------------------------------- #

def build_bank_index(rows: list[dict]) -> tuple[dict, list[dict]]:
    """
    Build a lookup index for fast matching.

    Returns:
      index_by_gref  — {G-ref: row}  for primary matching
      no_ref_rows    — rows without G-ref, sorted by amount, for amount fallback
    """
    index_by_gref: dict[str, dict] = {}
    no_ref_rows: list[dict] = []

    for row in rows:
        gref = row.get("gref", "")
        if gref:
            if gref not in index_by_gref:
                index_by_gref[gref] = row
        else:
            no_ref_rows.append(row)

    # Sort no-ref rows by amount so binary search is possible if needed
    no_ref_rows.sort(key=lambda r: r["amount_czk"])
    logger.debug(
        "Bank index: %d by G-ref, %d without ref (amount fallback pool)",
        len(index_by_gref), len(no_ref_rows),
    )
    return index_by_gref, no_ref_rows


# --------------------------------------------------------------------------- #
#  Matching                                                                    #
# --------------------------------------------------------------------------- #

def match_bank_transaction(
    gref: str,
    amount_czk: float,
    payout_date_str: str,
    index_by_gref: dict,
    no_ref_rows: list[dict],
    *,
    used_tx_keys: set[str] | None = None,
) -> dict | None:
    """
    Find the bank transaction for an Airbnb payout.

    Strategy: G-ref exact match only.
    Amount/date fallback has been removed — per §15.1 of the technical spec,
    weak fallback matching must not be used as automatic bank confirmation.

    Returns the matching bank row dict, or None if not found.
    """
    used_tx_keys = used_tx_keys or set()

    if gref and gref in index_by_gref:
        bank_row = index_by_gref[gref]
        if bank_row.get("tx_key") not in used_tx_keys:
            return bank_row

    return None


def _normalize_booking_ref(text: str) -> str:
    """
    Normalize a Booking reference for comparison.

    Booking payout CSV "Deskriptor výpisu":  "jR3ESa8TRKwKcGAt"
    Bank "Zpráva pro příjemce":              "NO.JR3ESA8TRKWKCGAT/10936099"

    Normalization: upper() → strip "NO." prefix → take part before first "/" → strip
    """
    s = (text or "").strip().upper()
    if s.startswith("NO."):
        s = s[3:]
    s = s.split("/")[0].strip()
    return s


def match_booking_by_ref(
    batch_ref: str,
    pid_rows: list[dict],
    *,
    used_tx_keys: set[str] | None = None,
) -> dict | None:
    """
    Match one Booking payout batch to a bank transaction by normalized descriptor reference.

    Per §15.2 of the technical spec: only reference-based matching is allowed.
    Amount/date fallback is not used.

    Returns the matching bank row dict, or None if not found.
    """
    used_tx_keys = used_tx_keys or set()
    normalized = _normalize_booking_ref(batch_ref)
    if not normalized:
        return None

    for row in pid_rows:
        if row.get("tx_key") in used_tx_keys:
            continue
        if row.get("booking_ref") == normalized:
            return row

    return None


# --------------------------------------------------------------------------- #
#  Enrichment                                                                  #
# --------------------------------------------------------------------------- #

def enrich_rows_with_bank(
    calc_rows: list[dict],
    gref_map: dict[str, dict],
    index_by_gref: dict,
    no_ref_rows: list[dict],
    all_batches_map: dict[str, list[dict]] | None = None,
    bank_index_full: dict | None = None,
    bank_no_ref_full: list | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Add bank arrival info to each calculated row.

    gref_map: {confirmation_code: {"gref": "G-XXXXX", "payout_date": "MM/DD/YYYY", "payout_czk": float}}
    all_batches_map: {confirmation_code: [{"gref": ..., "payout_date": ..., "payout_czk": ...}, ...]}
    Adds fields to each row:
        payout_gref     — "G-XXXXXXX" or ""
        bank_datum      — "DD.MM.YYYY" or ""
        bank_amount_czk — float (full batch, all properties)
        bank_status     — "DORAZILO" | "CHYBÍ" | "N/A" (non-Airbnb)
    """
    all_batches_map = all_batches_map or {}
    used_tx_keys: set[str] = set()
    batch_matches: dict[str, dict | None] = {}
    match_details: list[dict] = []

    for row in calc_rows:
        source = (row.get("source") or "").lower()
        if "airbnb" not in source:
            continue
        code = row.get("confirmation_code", "")
        payout_info = gref_map.get(code, {})
        gref = row.get("batch_ref") or payout_info.get("gref", "")
        payout_date_str = row.get("batch_payout_date") or payout_info.get("payout_date", "")
        payout_czk = row.get("batch_amount_czk_expected")
        if payout_czk is None:
            payout_czk = payout_info.get("payout_czk", 0.0)
        batch_key = gref or f"AIRBNB:{code}"
        if batch_key in batch_matches:
            continue
        bank_row = match_bank_transaction(
            gref, payout_czk, payout_date_str, index_by_gref, no_ref_rows,
            used_tx_keys=used_tx_keys,
        )
        # Fallback: adjustment/split/aircover rows may have bank txns beyond cutoff
        if bank_row is None and bank_index_full and gref and (
            row.get("is_payout_adjustment") or row.get("is_split_transaction") or row.get("is_aircover")
        ):
            bank_row = match_bank_transaction(
                gref, payout_czk, payout_date_str,
                bank_index_full, bank_no_ref_full or [],
                used_tx_keys=used_tx_keys,
            )
        batch_matches[batch_key] = bank_row
        if bank_row:
            used_tx_keys.add(bank_row.get("tx_key", ""))
            match_details.append({
                "batch_ref": gref,
                "tx_key": bank_row.get("tx_key", ""),
                "match_method": "gref" if gref else "amount_date",
                "matched_amount_czk": bank_row.get("amount_czk"),
            })

    # Match ALL batches for codes that have multiple payouts (split-payout)
    # so every gref gets a payout_batch_bank_matches entry.
    for row in calc_rows:
        source = (row.get("source") or "").lower()
        if "airbnb" not in source:
            continue
        code = row.get("confirmation_code", "")
        for batch_info in all_batches_map.get(code, []):
            extra_gref = batch_info.get("gref", "")
            if not extra_gref or extra_gref in batch_matches:
                continue
            bank_row = match_bank_transaction(
                extra_gref, batch_info.get("payout_czk", 0.0),
                batch_info.get("payout_date", ""),
                index_by_gref, no_ref_rows,
                used_tx_keys=used_tx_keys,
            )
            batch_matches[extra_gref] = bank_row
            if bank_row:
                used_tx_keys.add(bank_row.get("tx_key", ""))
                match_details.append({
                    "batch_ref": extra_gref,
                    "tx_key": bank_row.get("tx_key", ""),
                    "match_method": "gref",
                    "matched_amount_czk": bank_row.get("amount_czk"),
                })

    enriched = []
    for row in calc_rows:
        code = row.get("confirmation_code", "")
        source = (row.get("source") or "").lower()

        if "airbnb" not in source:
            enriched.append({
                **row,
                "payout_gref":     row.get("batch_ref", ""),
                "bank_datum":      row.get("bank_datum", ""),
                "bank_amount_czk": row.get("bank_amount_czk"),
                "bank_status":     row.get("bank_status", "N/A"),
            })
            continue

        payout_info = gref_map.get(code, {})
        gref = row.get("batch_ref") or payout_info.get("gref", "")
        payout_date_str = row.get("batch_payout_date") or payout_info.get("payout_date", "")
        payout_czk = row.get("batch_amount_czk_expected")
        if payout_czk is None:
            payout_czk = payout_info.get("payout_czk", 0.0)
        batch_key = gref or f"AIRBNB:{code}"
        bank_row = batch_matches.get(batch_key)

        if bank_row:
            datum = bank_row.get("datum")
            enriched.append({
                **row,
                "batch_ref": gref,
                "batch_payout_date": payout_date_str,
                "batch_amount_czk_expected": payout_czk,
                "payout_gref":     gref,
                "bank_tx_key":     bank_row.get("tx_key", ""),
                "bank_datum":      datum.strftime("%d.%m.%Y") if datum else "",
                "bank_amount_czk": bank_row["amount_czk"],
                "bank_status":     "DORAZILO",
            })
        else:
            enriched.append({
                **row,
                "batch_ref": gref,
                "batch_payout_date": payout_date_str,
                "batch_amount_czk_expected": payout_czk,
                "payout_gref":     gref,
                "bank_tx_key":     "",
                "bank_datum":      "",
                "bank_amount_czk": None,
                "bank_status":     "CHYBÍ",
            })

    arrived = sum(1 for r in enriched if r.get("bank_status") == "DORAZILO")
    missing = sum(1 for r in enriched if r.get("bank_status") == "CHYBÍ")
    logger.info("Bank enrichment: %d DORAZILO, %d CHYBÍ", arrived, missing)
    return enriched, match_details


# --------------------------------------------------------------------------- #
#  Pending payment resolution                                                  #
# --------------------------------------------------------------------------- #

def resolve_pending_against_bank(
    pending_rows: list[dict],
    index_by_gref: dict,
    no_ref_rows: list[dict],
    booking_bank_idx: dict[str, list[dict]],
    cutoff_date,
    booking_property_id: str = "",
) -> tuple[list[dict], list[dict]]:
    """
    Try to match pending payments (from DB) against bank transactions up to cutoff.

    Returns:
      resolved   — pending rows now matched (include bank_datum, bank_amount_czk)
      still_open — pending rows still unmatched
    """
    resolved = []
    still_open = []

    for p in pending_rows:
        source = (p.get("source") or "").lower()
        bank_row = None

        if "airbnb" in source:
            gref = p.get("gref", "")
            bank_row = match_bank_transaction(
                gref,
                0.0,  # amount/date fallback disabled — G-ref only
                "",
                index_by_gref,
                [],
            )

        elif "booking" in source:
            pid_rows = booking_bank_idx.get(booking_property_id, [])
            filtered_rows = [
                br for br in pid_rows
                if br.get("datum") and br["datum"] <= cutoff_date
            ]
            # Use the stored gref as the batch descriptor reference
            bank_row = match_booking_by_ref(p.get("gref", ""), filtered_rows)

        if bank_row:
            datum = bank_row.get("datum")
            resolved.append({
                **p,
                "bank_datum":      datum.strftime("%d.%m.%Y") if datum else "",
                "bank_amount_czk": p.get("expected_czk") or bank_row["amount_czk"],
                "bank_batch_amount_czk": bank_row["amount_czk"],
                "gref":            p.get("gref") or bank_row.get("zprava", ""),
            })
        else:
            still_open.append(p)

    return resolved, still_open


# --------------------------------------------------------------------------- #
#  Booking.com bank reconciliation                                             #
# --------------------------------------------------------------------------- #

def load_booking_bank_transactions(paths: list) -> dict[str, list[dict]]:
    """
    Load Booking.com bank transactions from bank CSV files.
    Message format: "NO.XXXXX/{property_id}"

    Returns {property_id: [sorted list of bank rows]}
    Each bank row: {datum, amount_czk, property_id, zprava, tx_id}
    """
    index: dict[str, list[dict]] = {}

    for path in paths:
        label = _source_name(path)
        if isinstance(path, str) and not os.path.exists(path):
            continue
        try:
            with _source_text(path, "utf-16") as f:
                for row in csv.DictReader(f):
                    if row.get("Typ transakce", "").strip() != "Příchozí úhrada":
                        continue
                    protiucet = row.get("Název protiúčtu", "").upper()
                    if "BOOKING.COM" not in protiucet:
                        continue
                    amount = _safe_float(row.get("Částka", 0))
                    if amount <= 0:
                        continue
                    zprava = row.get("Zpráva pro příjemce", "").strip()
                    # Extract property_id: "NO.XXXXX/{property_id}"
                    m = re.search(r"/(\d+)$", zprava)
                    if not m:
                        continue
                    property_id = m.group(1)
                    datum = _parse_date(row.get("Datum zaúčtování", "").strip())
                    entry = {
                        "datum":        datum,
                        "amount_czk":   amount,
                        "property_id":  property_id,
                        "booking_ref":  _normalize_booking_ref(zprava),
                        "zprava":       zprava,
                        "tx_id":        row.get("ID transakce", "").strip(),
                        "source_name":  label,
                    }
                    entry["tx_key"] = _bank_tx_key(entry)
                    index.setdefault(property_id, []).append(entry)
        except Exception as e:
            logger.error("Error reading bank CSV for Booking %s: %s", label, e)

    total = sum(len(v) for v in index.values())
    logger.info("Loaded %d Booking.com bank transactions for %d properties", total, len(index))
    return index


def enrich_booking_rows_with_bank(
    calc_rows: list[dict],
    booking_bank_idx: dict[str, list[dict]],
    property_config: dict,
    *,
    year: int | None = None,
    month: int | None = None,
    booking_bank_idx_all: dict[str, list[dict]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """
    Add bank arrival info to Booking.com rows.

    Matching is strict by Booking descriptor reference (`Deskriptor vypisu`) after
    resolving the property's Booking `property_id` from the canonical config shape.
    Updates payout_gref / bank_datum / bank_status for Booking rows.
    Airbnb rows are returned unchanged.

    booking_bank_idx_all: full (unfiltered) bank index used as a fallback when the
    cutoff-filtered index has no match — covers manually moved reservations whose
    bank payment arrives outside the target month's cutoff window.
    """
    from report.config import get_booking_config

    booking_cfg = get_booking_config(property_config, year=year, month=month)
    booking_pid = (
        booking_cfg.get("property_id")
        or property_config.get("property_id", "")
        or property_config.get("booking_property_id", "")
    )
    pid_rows = booking_bank_idx.get(booking_pid, []) if booking_pid else []
    all_booking_rows = [
        row
        for rows in booking_bank_idx.values()
        for row in rows
    ]
    pid_rows_all = (booking_bank_idx_all or {}).get(booking_pid, []) if booking_pid else []
    all_booking_rows_full = [
        row
        for rows in (booking_bank_idx_all or {}).values()
        for row in rows
    ]
    used_tx_keys: set[str] = set()
    batch_matches: dict[str, tuple[dict | None, str]] = {}
    match_details: list[dict] = []

    for row in calc_rows:
        source = (row.get("source") or "").lower()
        if "booking" not in source:
            continue
        batch_ref = row.get("batch_ref") or row.get("confirmation_code", "")
        if batch_ref in batch_matches:
            continue
        bank_row = match_booking_by_ref(batch_ref, pid_rows, used_tx_keys=used_tx_keys)
        match_method = "descriptor_ref"
        if bank_row is None:
            bank_row = match_booking_by_ref(batch_ref, all_booking_rows, used_tx_keys=used_tx_keys)
            match_method = "descriptor_ref_global"
        # Fallback: try full (unfiltered) bank data for manually-moved reservations
        # whose payment arrived outside the current month's cutoff window.
        if bank_row is None and booking_bank_idx_all is not None:
            bank_row = match_booking_by_ref(batch_ref, pid_rows_all, used_tx_keys=used_tx_keys)
            match_method = "descriptor_ref_full"
            if bank_row is None:
                bank_row = match_booking_by_ref(batch_ref, all_booking_rows_full, used_tx_keys=used_tx_keys)
                match_method = "descriptor_ref_full_global"
        batch_matches[batch_ref] = (bank_row, match_method)
        if bank_row:
            used_tx_keys.add(bank_row.get("tx_key", ""))
            match_details.append({
                "batch_ref": batch_ref,
                "tx_key": bank_row.get("tx_key", ""),
                "match_method": match_method,
                "matched_amount_czk": bank_row.get("amount_czk"),
            })

    enriched = []
    for row in calc_rows:
        source = (row.get("source") or "").lower()
        if "booking" not in source:
            enriched.append(row)
            continue

        batch_ref = row.get("batch_ref") or row.get("confirmation_code", "")
        matched_bank, _ = batch_matches.get(batch_ref, (None, ""))

        if matched_bank:
            datum = matched_bank.get("datum")
            enriched.append({
                **row,
                "payout_gref": batch_ref,
                "bank_tx_key": matched_bank.get("tx_key", ""),
                "bank_datum":  datum.strftime("%d.%m.%Y") if datum else "",
                "bank_amount_czk": matched_bank["amount_czk"],
                "bank_status": "DORAZILO",
            })
        else:
            enriched.append({
                **row,
                "payout_gref": batch_ref,
                "bank_tx_key": "",
                "bank_datum":  "",
                "bank_amount_czk": None,
                "bank_status": "CHYBÍ",
            })

    return enriched, match_details
