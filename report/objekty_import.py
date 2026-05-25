"""report/objekty_import.py — import Objekty.tsv into month-versioned object profiles.

Objekty.tsv is the single source of truth for the FV generator. Importing it for a
report month M writes a profile segment "from M onward" for each matched object whose
profile-relevant fields differ from the segment currently covering M. The apply is
idempotent (desired-vs-current comparison), so re-importing the same data is a no-op.

Rates (city_tax_rate / balicky_per_person / vat_rate / rentero_commission) are NOT in
the TSV and are carried forward from the prior segment. `todo` rows are skipped entirely.
Unmatched rows are reported, never applied. See
docs/superpowers/specs/2026-05-25-object-profiles-and-recurring-expenses-design.md
"""
from __future__ import annotations

import csv
import re
import unicodedata

from report.db_object_profiles import (
    get_object_profile, set_profile_from_month_onward,
)

CATEGORY_TO_CLIENT_TYPE = {
    "rentero": "rentero",
    "standard": "klient",
    "zrezim": "z_klient",
}

# Profile fields the TSV is authoritative for (used for change detection + write).
# Rates / email / phone / notes are intentionally omitted → carried forward.
_TSV_FIELDS = ("owner_name", "ico", "dic", "platce_dph", "adresa",
               "bank_account", "client_type", "stredisko", "active")


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text.lower().strip())


def _decode(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1250"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _format_address(ulice: str, misto: str, psc: str) -> str:
    tail = f"{psc} {misto}".strip()
    parts = [p.strip() for p in (ulice, tail) if p.strip()]
    return ", ".join(parts)


def _format_bank_account(ucet: str, kod_banky: str) -> str:
    ucet, kod = (ucet or "").strip(), (kod_banky or "").strip()
    return f"{ucet}/{kod}" if ucet and kod else (ucet or "")


def parse_objekty_tsv(content: bytes) -> list[dict]:
    """Parse the TSV into raw row dicts. Skips `#` comment lines."""
    text = _decode(content)
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines, delimiter="\t")
    rows: list[dict] = []
    for r in reader:
        rows.append({
            "category": (r.get("category") or "").strip().lower(),
            "stredisko": (r.get("stredisko") or "").strip(),
            "canonical_name": (r.get("canonical_name") or "").strip(),
            "owner_name": (r.get("owner_name") or "").strip(),
            "ico": (r.get("ico") or "").strip(),
            "platce_dph": (r.get("platce_dph") or "").strip(),
            "dic": (r.get("dic") or "").strip(),
            "aliases": (r.get("aliases") or ""),
            "ulice": (r.get("ulice") or "").strip(),
            "misto": (r.get("misto") or "").strip(),
            "psc": (r.get("psc") or "").strip(),
            "ucet": (r.get("ucet") or "").strip(),
            "kod_banky": (r.get("kod_banky") or "").strip(),
            "internet": (r.get("internet") or "").strip(),
            "ost_sluzby": (r.get("ost_sluzby") or "").strip(),
            "ost_sluzby_popis": (r.get("ost_sluzby_popis") or "").strip(),
            "ost_sluzby2": (r.get("ost_sluzby2") or "").strip(),
            "ost_sluzby2_popis": (r.get("ost_sluzby2_popis") or "").strip(),
        })
    return rows


def _build_slug_index(conn) -> dict[str, str]:
    index: dict[str, str] = {}
    for row in conn.execute("SELECT slug, display_name FROM report_objects").fetchall():
        slug = row["slug"]
        dn = _normalize(row["display_name"])
        if dn:
            index[dn] = slug
        index[_normalize(slug.replace("_", " "))] = slug
    for row in conn.execute(
        "SELECT report_object_slug, alias_value FROM report_object_aliases"
    ).fetchall():
        av = _normalize(row["alias_value"])
        if av:
            index.setdefault(av, row["report_object_slug"])
    return index


def _match_slug(index: dict[str, str], canonical_name: str, aliases_str: str) -> str | None:
    candidates = [canonical_name] + [a.strip() for a in (aliases_str or "").split("|") if a.strip()]
    for cand in candidates:
        norm = _normalize(cand)
        if norm in index:
            return index[norm]
    return None


def _desired_changes(row: dict) -> dict:
    """Profile changes the TSV asserts for a matched, non-todo row."""
    return {
        "owner_name": row["owner_name"],
        "ico": row["ico"],
        "dic": row["dic"],
        "platce_dph": 1 if (row["platce_dph"] or "0") not in ("", "0") else 0,
        "adresa": _format_address(row["ulice"], row["misto"], row["psc"]),
        "bank_account": _format_bank_account(row["ucet"], row["kod_banky"]),
        "client_type": CATEGORY_TO_CLIENT_TYPE.get(row["category"], "klient"),
        "stredisko": row["stredisko"],
        "active": 1,
    }


def _segment_differs(changes: dict, seg: dict | None) -> bool:
    if seg is None:
        return True
    for k, v in changes.items():
        cur = seg.get(k)
        if k in ("platce_dph", "active"):
            if int(cur or 0) != int(v or 0):
                return True
        else:
            if str(cur or "") != str(v or ""):
                return True
    return False


def _ym_to_int(effective_ym: str) -> tuple[int, int]:
    return int(effective_ym[:4]), int(effective_ym[5:7])


def _affected_month_keys(conn, slug: str, year: int, month: int) -> list[tuple[str, int, int]]:
    """Months >= (year, month) for this slug that already have report data."""
    m = f"{year:04d}-{month:02d}"
    rows = conn.execute(
        """SELECT year, month FROM report_month_state
           WHERE slug = ?
             AND printf('%04d-%02d', year, month) >= ?
             AND (last_generated_at IS NOT NULL OR data_state NOT IN ('', 'EMPTY'))""",
        (slug, m),
    ).fetchall()
    return [(slug, int(r["year"]), int(r["month"])) for r in rows]


def _scan(conn, content: bytes, effective_ym: str):
    """Shared scan: returns (year, month, changed, unchanged, unmatched, skipped, matched).
    `matched` is every (slug, row) whose object exists (changed + unchanged), used for
    TSV expense materialization which must run regardless of profile change."""
    year, month = _ym_to_int(effective_ym)
    index = _build_slug_index(conn)
    rows = parse_objekty_tsv(content)
    changed, unchanged, unmatched, skipped, matched = [], [], [], [], []
    for row in rows:
        cat = row["category"]
        if cat == "todo" or cat not in CATEGORY_TO_CLIENT_TYPE:
            skipped.append(row["canonical_name"])
            continue
        slug = _match_slug(index, row["canonical_name"], row["aliases"])
        if not slug:
            unmatched.append(row["canonical_name"])
            continue
        matched.append((slug, row))
        seg = get_object_profile(conn, slug, year, month)
        changes = _desired_changes(row)
        if _segment_differs(changes, seg):
            changed.append((slug, row, changes))
        else:
            unchanged.append(slug)
    return year, month, changed, unchanged, unmatched, skipped, matched


def _parse_amount(s: str) -> float:
    s = (s or "").strip().replace(" ", "").replace(",", ".")
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _net_dph_gross(net: float) -> tuple[float, float, float]:
    """TSV amounts are net (bez DPH); always add 21 %."""
    net = round(net, 2)
    dph = round(net * 0.21, 2)
    return net, dph, round(net + dph, 2)


def _apply_tsv_expenses(conn, slug: str, row: dict, year: int, month: int) -> None:
    """internet → recurring 'tsv:internet' template; ost_sluzby(2) → one-off expense
    for this month (deduped). All amounts net + 21 % DPH."""
    from report.db_admin import add_expense
    from report.db_expense_templates import upsert_tsv_template

    internet = _parse_amount(row.get("internet", ""))
    if internet:
        net, dph, gross = _net_dph_gross(internet)
        upsert_tsv_template(conn, slug, "tsv:internet", {
            "description": "Internet", "category_id": None,
            "amount_net_czk": net, "amount_dph_czk": dph, "amount_czk": gross,
            "vat_rate": 0.21, "start_ym": _ym_to_str(year, month), "end_ym": None,
        })
    else:
        # internet removed from TSV → deactivate any existing tsv:internet template.
        conn.execute(
            "UPDATE expense_templates SET active = 0 WHERE property_slug = ? AND source = 'tsv:internet'",
            (slug,),
        )

    for amount_key, popis_key, default_desc in (
        ("ost_sluzby", "ost_sluzby_popis", "Ostatní služby"),
        ("ost_sluzby2", "ost_sluzby2_popis", "Ostatní služby 2"),
    ):
        amt = _parse_amount(row.get(amount_key, ""))
        if not amt:
            continue
        net, dph, gross = _net_dph_gross(amt)
        desc = (row.get(popis_key) or "").strip() or default_desc
        # Dedup: same description + gross already present this month → skip.
        if conn.execute(
            """SELECT 1 FROM expenses
               WHERE property_slug=? AND year=? AND month=? AND description=?
                 AND ROUND(amount_czk, 2)=ROUND(?, 2) LIMIT 1""",
            (slug, year, month, desc, gross),
        ).fetchone():
            continue
        add_expense(conn, {
            "property_slug": slug, "year": year, "month": month, "date": None,
            "category_id": None, "description": desc,
            "amount_czk": gross, "amount_net_czk": net, "amount_dph_czk": dph,
            "vat_rate": 0.21,
        })


def _ym_to_str(year: int, month: int) -> str:
    return f"{int(year):04d}-{int(month):02d}"


def objekty_delta_summary(conn, content: bytes, effective_ym: str) -> dict:
    """Read-only: what an import for effective_ym WOULD change. Writes nothing."""
    year, month, changed, unchanged, unmatched, skipped, _matched = _scan(conn, content, effective_ym)
    return {
        "duplicate": False,
        "effective_ym": effective_ym,
        "updated_count": len(changed),
        "unchanged_count": len(unchanged),
        "unmatched_count": len(unmatched),
        "unmatched": unmatched,
        "skipped_todo_count": len(skipped),
        "detected_rows_count": len(changed) + len(unchanged) + len(unmatched) + len(skipped),
        "new_rows_count": len(changed),
        "new_transactions_count": 0,
        "new_reservations_count": 0,
        "affected_months": [],
        "affected_month_keys": [],
        "message": (
            f"Objekty {effective_ym}: {len(changed)} změněných, "
            f"{len(unmatched)} nespárováno, {len(skipped)} todo přeskočeno"
        ),
    }


def apply_objekty_import(conn, content: bytes, effective_ym: str) -> dict:
    """Write profile segments for changed objects, effective from effective_ym."""
    year, month, changed, unchanged, unmatched, skipped, matched = _scan(conn, content, effective_ym)
    updated_slugs: list[str] = []
    affected_month_keys: list[tuple[str, int, int]] = []
    for slug, row, changes in changed:
        set_profile_from_month_onward(conn, slug, year, month, changes, source="tsv")
        updated_slugs.append(slug)
        affected_month_keys.extend(_affected_month_keys(conn, slug, year, month))
    # TSV auto-expenses run for ALL matched rows (internet template + ost_sluzby
    # one-offs), regardless of whether the profile changed.
    for slug, row in matched:
        _apply_tsv_expenses(conn, slug, row, year, month)
    return {
        "duplicate": False,
        "effective_ym": effective_ym,
        "updated_slugs": updated_slugs,
        "updated_count": len(updated_slugs),
        "unchanged_count": len(unchanged),
        "unmatched_count": len(unmatched),
        "unmatched": unmatched,
        "skipped_todo_count": len(skipped),
        "detected_rows_count": len(changed) + len(unchanged) + len(unmatched) + len(skipped),
        "new_rows_count": len(updated_slugs),
        "new_transactions_count": 0,
        "new_reservations_count": 0,
        "affected_months": sorted({(y, m) for _s, y, m in affected_month_keys}),
        "affected_month_keys": affected_month_keys,
        "message": (
            f"Objekty {effective_ym}: {len(updated_slugs)} objektů aktualizováno, "
            f"{len(unmatched)} nespárováno, {len(skipped)} todo přeskočeno"
        ),
    }
