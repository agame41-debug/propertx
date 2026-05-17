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

def _find_code_in_other_snapshots(
    conn,
    code: str,
    slug: str,
    year: int,
    month: int,
    limit: int = 5,
) -> list[tuple[str, int, int]]:
    """L2 integrity defense: locate the same confirmation_code in other
    (slug, year, month) snapshots. Empty codes never trigger.

    Uses idx_report_rows_code_lookup for the lookup."""
    if not code:
        return []
    if not slug or not year or not month:
        # Caller didn't provide a valid snapshot context — can't compute "other"
        return []
    rows = conn.execute(
        """SELECT slug, year, month FROM report_rows
           WHERE confirmation_code = ?
             AND NOT (slug = ? AND year = ? AND month = ?)
           ORDER BY year DESC, month DESC
           LIMIT ?""",
        (code, slug, year, month, limit),
    ).fetchall()
    return [(r["slug"], r["year"], r["month"]) for r in rows]


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


def _source_bytes(source) -> bytes:
    """Raw bytes for a path or an archived source dict."""
    if isinstance(source, str):
        with open(source, "rb") as fh:
            return fh.read()
    content = source.get("content") or b""
    if isinstance(content, memoryview):
        content = content.tobytes()
    return bytes(content)


def _detect_bank_format(raw: bytes) -> str:
    """Return 'legacy' (UTF-16/comma), 'money_s3' (cp1250/;), or 'unknown'."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "legacy"
    head = raw[:4096].decode("cp1250", errors="replace")
    if head.lstrip().startswith("Header;") or "Detail 1;" in head:
        return "money_s3"
    return "unknown"


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


# Money S3 column names (after cp1250 decode) used to build the index map.
_MS3_COL_AMOUNT = "Částka"
_MS3_COL_DATE = "Datum zaúčtování"
_MS3_COL_MESSAGE = "Doplňující údaj"
_MS3_COL_TXID = "Identifikátor položky"
_MS3_COL_PARTNER = "Název protiúčtu"
# Fixed 0-based indices used if the "Detail 1;0;..." definition row is absent.
_MS3_FALLBACK_IDX = {
    _MS3_COL_AMOUNT: 3,
    _MS3_COL_DATE: 9,
    _MS3_COL_MESSAGE: 10,
    _MS3_COL_TXID: 11,
    _MS3_COL_PARTNER: 14,
}


def _iter_bank_records(source):
    """Yield unified bank records regardless of source format.

    Record keys: datum (date|None), amount_czk (float), counterparty (str),
    message (str), ref_secondary (str), tx_id (str), transaction_type (str).
    """
    label = _source_name(source)
    if isinstance(source, str) and not os.path.exists(source):
        logger.warning("Bank CSV not found: %s", source)
        return
    try:
        raw = _source_bytes(source)
    except Exception as e:
        logger.error("Error reading bank source %s: %s", label, e)
        return
    if not raw:
        return

    fmt = _detect_bank_format(raw)
    if fmt == "legacy":
        text = raw.decode("utf-16", errors="replace")
        for row in csv.DictReader(io.StringIO(text)):
            yield {
                "datum": _parse_date(row.get("Datum zaúčtování", "").strip()),
                "amount_czk": _safe_float(row.get("Částka", 0)),
                "counterparty": row.get("Název protiúčtu", ""),
                "message": row.get("Zpráva pro příjemce", "").strip(),
                "ref_secondary": row.get("Reference platby", "").strip(),
                "tx_id": row.get("ID transakce", "").strip(),
                "transaction_type": row.get("Typ transakce", "").strip(),
            }
    elif fmt == "money_s3":
        text = raw.decode("cp1250", errors="replace")
        reader = csv.reader(io.StringIO(text), delimiter=";")
        colmap: dict[str, int] = {}
        for cols in reader:
            if len(cols) < 2 or cols[0].strip() != "Detail 1":
                continue
            marker = cols[1].strip()
            if marker == "0":
                colmap = {name.strip(): i for i, name in enumerate(cols)}
                continue
            if marker != "1":
                continue

            def _get(name: str) -> str:
                idx = colmap.get(name, _MS3_FALLBACK_IDX.get(name))
                if idx is None or idx >= len(cols):
                    return ""
                return cols[idx].strip()

            yield {
                "datum": _parse_date(_get(_MS3_COL_DATE)),
                "amount_czk": _safe_float(_get(_MS3_COL_AMOUNT)),
                "counterparty": _get(_MS3_COL_PARTNER),
                "message": _get(_MS3_COL_MESSAGE),
                "ref_secondary": "",
                "tx_id": _get(_MS3_COL_TXID),
                "transaction_type": "",
            }
    else:
        logger.error("Unrecognized bank file format: %s", label)
        return


def load_bank_csv(paths: list) -> list[dict]:
    """
    Load Airbnb bank transactions (CITIBANK EUROPE incoming) from any
    supported bank-statement format. Returns normalised bank row dicts.
    """
    rows: list[dict] = []

    for path in paths:
        label = _source_name(path)
        try:
            for rec in _iter_bank_records(path):
                if "CITIBANK EUROPE" not in rec["counterparty"].upper():
                    continue
                ttype = rec["transaction_type"]
                if ttype not in ("Příchozí úhrada", ""):
                    continue
                if rec["amount_czk"] <= 0:
                    continue
                gref = _extract_gref(rec["message"]) or _extract_gref(rec["ref_secondary"])
                rows.append({
                    "datum":      rec["datum"],
                    "amount_czk": rec["amount_czk"],
                    "gref":       gref,
                    "tx_id":      rec["tx_id"],
                    "zprava":     rec["message"],
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


# Conservative defaults for the no-G-ref fallback. Used only when Česká
# spořitelna omits the Citibank/Airbnb reference field and primary G-ref
# matching has already returned None. Matching proceeds only when exactly
# one bank tx in the no-ref pool sits in the date window with the same
# amount — multi-candidate cases are explicitly rejected.
_AMOUNT_FALLBACK_DATE_WINDOW_DAYS = 7
_AMOUNT_FALLBACK_AMOUNT_TOLERANCE_CZK = 0.01

# Distinct match_method value so fallback matches can be filtered/audited
# in payout_batch_bank_matches and visually marked in the UI.
MATCH_METHOD_AMOUNT_FALLBACK = "amount_date_fallback"


def match_bank_amount_date_fallback(
    target_amount_czk: float,
    target_payout_date_str: str,
    no_ref_rows: list[dict],
    *,
    used_tx_keys: set[str] | None = None,
    date_window_days: int = _AMOUNT_FALLBACK_DATE_WINDOW_DAYS,
    amount_tolerance_czk: float = _AMOUNT_FALLBACK_AMOUNT_TOLERANCE_CZK,
) -> dict | None:
    """Conservative amount + date fallback for bank tx that arrived without a G-ref.

    Returns the bank row only when EXACTLY ONE candidate exists in the no-G-ref
    pool within the date window and amount tolerance. Multi-candidate cases are
    rejected and logged. Caller MUST have already attempted G-ref matching and
    received None.
    """
    if target_amount_czk is None or target_amount_czk <= 0:
        return None
    target_date = _parse_date(target_payout_date_str or "")
    if target_date is None:
        return None
    used_tx_keys = used_tx_keys or set()

    candidates: list[dict] = []
    for row in no_ref_rows:
        if row.get("tx_key", "") in used_tx_keys:
            continue
        bank_amount = row.get("amount_czk", 0.0) or 0.0
        if abs(bank_amount - target_amount_czk) > amount_tolerance_czk:
            continue
        bank_date = row.get("datum")
        if not bank_date:
            continue
        if abs((bank_date - target_date).days) > date_window_days:
            continue
        candidates.append(row)

    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        logger.warning(
            "Bank amount-date fallback ambiguous: %.2f CZK around %s — %d candidates, skipping",
            target_amount_czk, target_payout_date_str, len(candidates),
        )
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
    *,
    conn=None,
    slug: str = "",
    year: int = 0,
    month: int = 0,
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
    # Parallel map: same key as batch_matches → match_method ("gref" or
    # MATCH_METHOD_AMOUNT_FALLBACK). Lets the per-row enrichment annotate
    # bank_match_method without re-querying the matcher.
    batch_match_methods: dict[str, str] = {}
    match_details: list[dict] = []

    def _try_match_with_fallback(target_gref, target_czk, target_payout_date_str,
                                 primary_index, primary_no_ref):
        """Run G-ref strict match, then conservative amount+date fallback.

        Returns (bank_row, match_method) where match_method is "gref",
        MATCH_METHOD_AMOUNT_FALLBACK, or "" (no match).
        """
        primary = match_bank_transaction(
            target_gref, target_czk, target_payout_date_str,
            primary_index, primary_no_ref,
            used_tx_keys=used_tx_keys,
        )
        if primary is not None:
            return primary, "gref"
        # Fallback only fires when we know which batch we're looking for
        # (target_gref non-empty) — otherwise the target is undefined.
        if not target_gref:
            return None, ""
        fallback = match_bank_amount_date_fallback(
            target_czk, target_payout_date_str, primary_no_ref,
            used_tx_keys=used_tx_keys,
        )
        if fallback is not None:
            logger.info(
                "Bank match by amount-date fallback: batch=%s tx=%s amount=%.2f bank_date=%s",
                target_gref, fallback.get("tx_key", ""),
                fallback.get("amount_czk", 0.0),
                fallback.get("datum"),
            )
            return fallback, MATCH_METHOD_AMOUNT_FALLBACK
        return None, ""

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
        bank_row, match_method_used = _try_match_with_fallback(
            gref, payout_czk, payout_date_str, index_by_gref, no_ref_rows,
        )
        # Cross-cutoff fallback: adjustment/split/aircover rows may have bank
        # txns beyond the per-month cutoff.
        if bank_row is None and bank_index_full and gref and (
            row.get("is_payout_adjustment") or row.get("is_split_transaction") or row.get("is_aircover")
        ):
            bank_row, match_method_used = _try_match_with_fallback(
                gref, payout_czk, payout_date_str,
                bank_index_full, bank_no_ref_full or [],
            )
        batch_matches[batch_key] = bank_row
        if bank_row:
            batch_match_methods[batch_key] = match_method_used
            used_tx_keys.add(bank_row.get("tx_key", ""))
            match_details.append({
                "batch_ref": gref,
                "tx_key": bank_row.get("tx_key", ""),
                "match_method": match_method_used,
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
            bank_row, match_method_used = _try_match_with_fallback(
                extra_gref, batch_info.get("payout_czk", 0.0),
                batch_info.get("payout_date", ""),
                index_by_gref, no_ref_rows,
            )
            batch_matches[extra_gref] = bank_row
            if bank_row:
                batch_match_methods[extra_gref] = match_method_used
                used_tx_keys.add(bank_row.get("tx_key", ""))
                match_details.append({
                    "batch_ref": extra_gref,
                    "tx_key": bank_row.get("tx_key", ""),
                    "match_method": match_method_used,
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
            verification_comment = row.get("verification_comment") or ""
            if conn is not None and slug:
                others = _find_code_in_other_snapshots(
                    conn, row.get("confirmation_code", ""), slug, year, month
                )
                if others:
                    where = ", ".join(f"{s}/{y}-{m:02d}" for s, y, m in others)
                    verification_comment = (
                        f"INTEGRITY: also in {where}. {verification_comment}".strip()
                    )
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
                "bank_match_method": batch_match_methods.get(batch_key, "gref"),
                "verification_comment": verification_comment,
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
                "bank_match_method": "",
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
        try:
            for rec in _iter_bank_records(path):
                if "BOOKING.COM" not in rec["counterparty"].upper():
                    continue
                ttype = rec["transaction_type"]
                if ttype not in ("Příchozí úhrada", ""):
                    continue
                if rec["amount_czk"] <= 0:
                    continue
                zprava = rec["message"]
                # Extract property_id: "NO.XXXXX/{property_id}"
                m = re.search(r"/(\d+)$", zprava)
                if not m:
                    continue
                property_id = m.group(1)
                entry = {
                    "datum":        rec["datum"],
                    "amount_czk":   rec["amount_czk"],
                    "property_id":  property_id,
                    "booking_ref":  _normalize_booking_ref(zprava),
                    "zprava":       zprava,
                    "tx_id":        rec["tx_id"],
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
    conn=None,
    slug: str = "",
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
            verification_comment = row.get("verification_comment") or ""
            if conn is not None and slug:
                others = _find_code_in_other_snapshots(
                    conn, row.get("confirmation_code", ""), slug, year, month
                )
                if others:
                    where = ", ".join(f"{s}/{y}-{m:02d}" for s, y, m in others)
                    verification_comment = (
                        f"INTEGRITY: also in {where}. {verification_comment}".strip()
                    )
            enriched.append({
                **row,
                "payout_gref": batch_ref,
                "bank_tx_key": matched_bank.get("tx_key", ""),
                "bank_datum":  datum.strftime("%d.%m.%Y") if datum else "",
                "bank_amount_czk": matched_bank["amount_czk"],
                "bank_status": "DORAZILO",
                "verification_comment": verification_comment,
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
