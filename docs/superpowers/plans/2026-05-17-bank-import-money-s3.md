# Bank Import Money S3 Format Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make bank-statement import accept the new Money S3 format (cp1250, `;`, multi-section) alongside the legacy UTF-16/comma format via auto-detection, and collect Marriott transactions into a new channel.

**Architecture:** Approach A — a single internal normalizing iterator (`_iter_bank_records`) owns all format knowledge and yields a unified record. `load_bank_csv` / `load_booking_bank_transactions` / new `load_marriott_bank_transactions` consume it and keep their channel filters. Strict Airbnb/Booking reference matching is untouched. Marriott is collect-only (persisted, not matched).

**Tech Stack:** Python 3, stdlib `csv`/`io`/`re`, pytest. Files: `report/bank.py`, `report/source_registry.py`, `tests/test_bank.py`.

**Spec:** `docs/superpowers/specs/2026-05-17-bank-import-money-s3-design.md`
**Branch:** `feat/bank-import-money-s3` (already checked out)

---

## File Structure

- `report/bank.py` — add `_source_bytes`, `_detect_bank_format`, `_iter_bank_records`; refactor `load_bank_csv` & `load_booking_bank_transactions` to consume the iterator; add `load_marriott_bank_transactions`.
- `report/source_registry.py` — persist `marriott` channel in the bank import branch and count it in `_bank_delta_summary`.
- `tests/test_bank.py` — add cp1250 byte-literal fixtures and tests for detection, money_s3 Airbnb/Booking/Marriott, legacy regression, header/footer skipping.

---

## Task 1: Raw-bytes helper and format detection

**Files:**
- Modify: `report/bank.py` (add helpers near `_source_text`, ~line 92-98)
- Test: `tests/test_bank.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_bank.py`:

```python
# ---------------------------------------------------------------------------
# Money S3 format support
# ---------------------------------------------------------------------------
from report.bank import (
    _detect_bank_format,
    _source_bytes,
    load_booking_bank_transactions,
    load_marriott_bank_transactions,
)

# Money S3: cp1250-encoded, ';'-separated, Header / Detail 1 / Footer sections.
# Detail definition row (col1==0) names the columns; data rows have col1==1.
_MS3_DETAIL_DEF = (
    "Detail 1;0;Banka protiúètu;Èástka;Èíslo bank. dokladu;"
    "Èíslo hrazeného dokladu;Èíslo protiúètu;Datum splatnosti;"
    "Datum vstupu do banky;Datum zaúètování;Doplòující údaj;"
    "Identifikátor poloky;Konstantní symbol;Mìna;Název protiúètu;"
    "Pøíznak spárování;Specifický symbol;Typ poloky;Variabilní symbol"
)


def _ms3_file(*detail_rows: str) -> dict:
    """Build a Money S3 cp1250 source blob: Header, Detail def, data rows, Footer."""
    lines = [
        "Header;0;Banka;Celkem;Èíslo strany",
        "Header;1;0800;0;1",
        _MS3_DETAIL_DEF,
        *detail_rows,
        "Footer;0;Celkem;Èíslo strany",
        "Footer;1;0;11;46148",
    ]
    content = ("\r\n".join(lines) + "\r\n").encode("cp1250")
    return {"original_name": "vypis_ms3.csv", "content": content, "id": 2}


def _ms3_detail(amount: str, datum: str, doplnujici: str, ident: str, partner: str) -> str:
    # Columns: 0 "Detail 1";1 marker;2 bank;3 amount;4-8;9 datum;10 doplnujici;
    #          11 ident;12-13;14 partner;15-18
    return (
        f"Detail 1;1;2600;{amount};;;4000230103;01.04.2026;;{datum};"
        f"{doplnujici};{ident};;CZK;{partner};0;;1;"
    )


def test_detect_format_legacy_utf16():
    raw = (BANK_HEADER + _make_bank_row()).encode("utf-16")
    assert _detect_bank_format(raw) == "legacy"


def test_detect_format_money_s3():
    raw = _ms3_file(
        _ms3_detail("100.00", "01.04.2026", "G-ABC payment", "TX9", "CITIBANK EUROPE PLC")
    )["content"]
    assert _detect_bank_format(raw) == "money_s3"


def test_source_bytes_from_blob():
    blob = {"original_name": "x.csv", "content": b"abc", "id": 7}
    assert _source_bytes(blob) == b"abc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bank.py::test_detect_format_money_s3 tests/test_bank.py::test_source_bytes_from_blob -v`
Expected: FAIL with `ImportError` / `cannot import name '_detect_bank_format'`.

- [ ] **Step 3: Implement helpers**

In `report/bank.py`, after `_source_text` (ends ~line 98), add:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bank.py::test_detect_format_legacy_utf16 tests/test_bank.py::test_detect_format_money_s3 tests/test_bank.py::test_source_bytes_from_blob -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add report/bank.py tests/test_bank.py
git commit -m "feat(bank): add _source_bytes + _detect_bank_format

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Normalizing iterator + refactor load_bank_csv (legacy parity)

**Files:**
- Modify: `report/bank.py` (`load_bank_csv` ~line 125-168; add `_iter_bank_records`)
- Test: `tests/test_bank.py` (existing `TestLoadBankCsv` is the regression guard)

- [ ] **Step 1: Add the iterator (legacy branch only)**

In `report/bank.py`, add before `load_bank_csv` (~line 125):

```python
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
```

- [ ] **Step 2: Refactor `load_bank_csv` to consume the iterator**

Replace the body of `load_bank_csv` (`report/bank.py:125-168`) with:

```python
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
```

- [ ] **Step 3: Run the legacy regression suite**

Run: `pytest tests/test_bank.py::TestLoadBankCsv -v`
Expected: all 9 tests pass (legacy parity preserved — `test_skips_non_incoming` still 0 rows because `"Odchozí platba"` is neither `"Příchozí úhrada"` nor `""`).

- [ ] **Step 4: Commit**

```bash
git add report/bank.py
git commit -m "refactor(bank): route load_bank_csv through _iter_bank_records

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Money S3 Airbnb path test

**Files:**
- Test: `tests/test_bank.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_bank.py`:

```python
def test_money_s3_airbnb_rows():
    src = _ms3_file(
        _ms3_detail("22798.84", "01.04.2026",
                    "G-GBTYRNNO42QHF/ROC/G-GBTYRNNO42QHF", "2000025518621917",
                    "CITIBANK EUROPE PLC"),
        _ms3_detail("27274.23", "01.04.2026",
                    "NO.JFM7YNCLTB8ZCFXX/13805101", "2000025514825816",
                    "BOOKING.COM B.V."),
    )
    rows = load_bank_csv([src])
    assert len(rows) == 1
    r = rows[0]
    assert r["amount_czk"] == 22798.84
    assert r["gref"] == "G-GBTYRNNO42QHF"
    assert r["datum"] == date(2026, 4, 1)
    assert r["tx_id"] == "2000025518621917"
    assert r["tx_key"]


def test_money_s3_skips_header_footer():
    # An owner payout (not CITIBANK/BOOKING) plus header/footer must be ignored.
    src = _ms3_file(
        _ms3_detail("63530.57", "15.04.2026",
                    "Výplata Rentero Property za byt Francouzská 50",
                    "2000025699874206", "Build with us s.r.o."),
    )
    assert load_bank_csv([src]) == []
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_bank.py::test_money_s3_airbnb_rows tests/test_bank.py::test_money_s3_skips_header_footer -v`
Expected: 2 passed (implementation already exists from Task 2; these lock in behavior).

- [ ] **Step 3: Commit**

```bash
git add tests/test_bank.py
git commit -m "test(bank): money_s3 Airbnb parse + header/footer skip

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Refactor load_booking_bank_transactions through the iterator

**Files:**
- Modify: `report/bank.py` (`load_booking_bank_transactions` ~line 606-654)
- Test: `tests/test_bank.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_bank.py`:

```python
def test_money_s3_booking_rows():
    src = _ms3_file(
        _ms3_detail("27274.23", "01.04.2026",
                    "NO.JFM7YNCLTB8ZCFXX/13805101", "2000025514825816",
                    "BOOKING.COM B.V."),
        _ms3_detail("22798.84", "01.04.2026",
                    "G-GBTYRNNO42QHF/ROC/G-GBTYRNNO42QHF", "2000025518621917",
                    "CITIBANK EUROPE PLC"),
    )
    idx = load_booking_bank_transactions([src])
    assert "13805101" in idx
    row = idx["13805101"][0]
    assert row["amount_czk"] == 27274.23
    assert row["booking_ref"] == "JFM7YNCLTB8ZCFXX"
    assert row["property_id"] == "13805101"
    assert row["datum"] == date(2026, 4, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bank.py::test_money_s3_booking_rows -v`
Expected: FAIL — old `load_booking_bank_transactions` reads only UTF-16/comma, returns empty index, `KeyError`/assert fails.

- [ ] **Step 3: Refactor `load_booking_bank_transactions`**

Replace the body of `load_booking_bank_transactions` (`report/bank.py:606-654`) with:

```python
def load_booking_bank_transactions(paths: list) -> dict[str, list[dict]]:
    """
    Load Booking.com bank transactions from any supported format.
    Message format: "NO.XXXXX/{property_id}". Returns {property_id: [rows]}.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bank.py::test_money_s3_booking_rows tests/test_bank.py::TestMatchBookingByRef tests/test_bank.py::TestEnrichBookingRowsWithBank -v`
Expected: all pass (new money_s3 path works; existing Booking matching unaffected).

- [ ] **Step 5: Commit**

```bash
git add report/bank.py tests/test_bank.py
git commit -m "refactor(bank): route Booking bank loader through _iter_bank_records

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Marriott collect-only loader

**Files:**
- Modify: `report/bank.py` (add `load_marriott_bank_transactions` after `load_booking_bank_transactions`)
- Test: `tests/test_bank.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_bank.py`:

```python
def test_money_s3_marriott_collected():
    src = _ms3_file(
        _ms3_detail("38812.54", "07.04.2026",
                    "/ROC/0000000W3O///URI//JPMPAY/M17 31.03.2026",
                    "2000025584466205", "Global Hospitality Licensing S.a"),
        _ms3_detail("8266.84", "10.04.2026",
                    "/ROC/0000000WB5///URI//JPMPAY/",
                    "2000025621938680", "Global Hospitality Licensing S.a"),
        _ms3_detail("27274.23", "01.04.2026",
                    "NO.JFM7YNCLTB8ZCFXX/13805101", "2000025514825816",
                    "BOOKING.COM B.V."),
    )
    rows = load_marriott_bank_transactions([src])
    assert len(rows) == 2
    r0 = rows[0]
    assert r0["amount_czk"] == 38812.54
    assert r0["gref"] == "0000000W3O"
    assert r0["datum"] == date(2026, 4, 7)
    assert r0["tx_id"] == "2000025584466205"
    assert r0["tx_key"]
    # Token-less still collected
    assert rows[1]["gref"] == "0000000WB5"


def test_marriott_without_roc_token_still_collected():
    src = _ms3_file(
        _ms3_detail("1000.00", "07.04.2026", "JPMPAY no token here",
                    "TXID1", "Global Hospitality Licensing S.a"),
    )
    rows = load_marriott_bank_transactions([src])
    assert len(rows) == 1
    assert rows[0]["gref"] == ""
    assert rows[0]["amount_czk"] == 1000.00
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bank.py::test_money_s3_marriott_collected -v`
Expected: FAIL — `cannot import name 'load_marriott_bank_transactions'`.

- [ ] **Step 3: Implement the loader**

In `report/bank.py`, add after `load_booking_bank_transactions` (~line 654):

```python
_MARRIOTT_ROC_RE = re.compile(r"/ROC/([A-Z0-9]+)")


def load_marriott_bank_transactions(paths: list) -> list[dict]:
    """
    Collect Marriott (Global Hospitality Licensing) bank transactions.

    Marriott has no payout CSV and no per-reservation reference, so these
    rows are persisted for visibility/audit only — NO matching is performed.
    """
    rows: list[dict] = []

    for path in paths:
        label = _source_name(path)
        try:
            for rec in _iter_bank_records(path):
                if "GLOBAL HOSPITALITY" not in rec["counterparty"].upper():
                    continue
                if rec["amount_czk"] <= 0:
                    continue
                m = _MARRIOTT_ROC_RE.search((rec["message"] or "").upper())
                ref = m.group(1) if m else ""
                row = {
                    "datum":       rec["datum"],
                    "amount_czk":  rec["amount_czk"],
                    "gref":        ref,
                    "property_id": "",
                    "zprava":      rec["message"],
                    "tx_id":       rec["tx_id"],
                    "source_name": label,
                }
                row["tx_key"] = _bank_tx_key(row)
                rows.append(row)
        except Exception as e:
            logger.error("Error reading bank CSV for Marriott %s: %s", label, e)

    logger.info("Collected %d Marriott bank transactions from %d file(s)", len(rows), len(paths))
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bank.py::test_money_s3_marriott_collected tests/test_bank.py::test_marriott_without_roc_token_still_collected -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add report/bank.py tests/test_bank.py
git commit -m "feat(bank): collect Marriott (Global Hospitality) transactions

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Persist Marriott channel in source_registry

**Files:**
- Modify: `report/source_registry.py` (import `load_marriott_bank_transactions`; bank import branch ~line 616-624; `_bank_delta_summary` ~line 324-399)
- Test: `tests/test_bank.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_bank.py`:

```python
def test_marriott_persisted_via_source_registry(tmp_path):
    from report.source_registry import import_uploaded_source

    conn = get_connection(str(tmp_path / "t.db"))
    lines = [
        "Header;0;Banka;Celkem", "Header;1;0800;0",
        _MS3_DETAIL_DEF,
        _ms3_detail("38812.54", "07.04.2026",
                    "/ROC/0000000W3O///URI//JPMPAY/M17 31.03.2026",
                    "2000025584466205", "Global Hospitality Licensing S.a"),
        "Footer;0;Celkem", "Footer;1;0;1",
    ]
    content = ("\r\n".join(lines) + "\r\n").encode("cp1250")
    import_uploaded_source(conn, "bank", "vypis_ms3.csv", content, imported_by="test")

    n = conn.execute(
        "SELECT COUNT(*) c FROM bank_transactions WHERE channel='marriott'"
    ).fetchone()["c"]
    assert n == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bank.py::test_marriott_persisted_via_source_registry -v`
Expected: FAIL — `channel='marriott'` count is 0 (not yet persisted).

- [ ] **Step 3: Wire the import branch**

In `report/source_registry.py`, line 11, extend the import:

```python
from report.bank import load_bank_csv, load_booking_bank_transactions, load_marriott_bank_transactions
```

In the bank import branch (`source_registry.py:616-624`), after the two existing `save_bank_transactions` calls and summary lines, add:

```python
            marriott_rows = load_marriott_bank_transactions([source])
            save_bank_transactions(conn, "marriott", marriott_rows, commit=False)
            summary["persisted_marriott_transactions"] = len(marriott_rows)
```

(Insert directly after `summary["persisted_booking_transactions"] = len(booking_rows)`.)

- [ ] **Step 4: Count Marriott in `_bank_delta_summary`**

In `report/source_registry.py`, inside `_bank_delta_summary` (~line 326-328), after `booking_rows = [...]` add:

```python
    marriott_rows = load_marriott_bank_transactions([source])
```

Then change the combined dedup loop (`for row in airbnb_rows + booking_rows:`, line 338) to:

```python
    for row in airbnb_rows + booking_rows + marriott_rows:
```

And update the message (line 395-398) to:

```python
        "message": (
            f"+{len(new_total)} new bank transactions "
            f"(Airbnb {len(new_airbnb)}, Booking {len(new_booking)}, "
            f"Marriott {len(marriott_rows)})"
        ),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_bank.py::test_marriott_persisted_via_source_registry -v`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add report/source_registry.py tests/test_bank.py
git commit -m "feat(bank): persist marriott channel + count in delta summary

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Full regression and final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full bank suite**

Run: `pytest tests/test_bank.py -q`
Expected: all pass (legacy + money_s3 + Marriott + persistence).

- [ ] **Step 2: Run source-import suite**

Run: `pytest tests/test_source_imports.py -q`
Expected: all pass (bank import branch still works for legacy format).

- [ ] **Step 3: Sanity-parse the real legacy file**

Run:
```bash
python -c "from report.bank import load_bank_csv; print(len(load_bank_csv(['source/bank/transakce CS 2024-2026.csv'])))"
```
Expected: a positive integer (legacy file still parses; no exception).

- [ ] **Step 4: Final commit if anything was adjusted**

```bash
git add -A
git commit -m "test(bank): full regression for Money S3 import support

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>" || echo "nothing to commit"
```

> Note: `tests/test_verifier.py` has 4 pre-existing unrelated failures (city_tax + month_assignment). Do not treat them as regressions.

---

## Self-Review

**Spec coverage:**
- Component 1 (detection + iterator) → Tasks 1, 2.
- Component 2 (consumers: load_bank_csv, load_booking_bank_transactions, load_marriott) → Tasks 2, 4, 5.
- Component 3 (source_registry persistence + delta) → Task 6.
- Component 4 (error handling: unknown format, missing def row fallback, cp1250 replace, dedup, Marriott token-less) → covered in Task 1/2 code (`_detect_bank_format` unknown branch, `_MS3_FALLBACK_IDX`, `errors="replace"`) and tested in Task 5 (`test_marriott_without_roc_token_still_collected`) and Task 3 (`test_money_s3_skips_header_footer`).
- Component 5 (tests) → Tasks 1,3,4,5,6 fixtures + Task 7 regression.

**Placeholder scan:** none — every code/test step contains full content.

**Type consistency:** unified record keys (`datum, amount_czk, counterparty, message, ref_secondary, tx_id, transaction_type`) are identical across `_iter_bank_records`, `load_bank_csv`, `load_booking_bank_transactions`, `load_marriott_bank_transactions`. Money S3 column constants (`_MS3_COL_*`) reused consistently. `_bank_tx_key`, `_extract_gref`, `_normalize_booking_ref`, `_parse_date`, `_safe_float` are pre-existing in `report/bank.py`.

No gaps found.
