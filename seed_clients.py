"""
One-off script: load client data from Objekty.tsv into the clients table.

Matches TSV rows to report_objects slugs via display_name / alias comparison.
Run:  python seed_clients.py [--dry-run]
"""
import csv
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone

DB_PATH = "cache/rentero.db"
TSV_PATH = "source/Objekty.tsv"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _normalize(text: str) -> str:
    """Normalize text for matching: strip diacritics, lowercase, collapse whitespace."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _build_slug_index(conn: sqlite3.Connection) -> dict[str, str]:
    """Build {normalized_display_name: slug} + {normalized_alias: slug}."""
    index: dict[str, str] = {}

    # From report_objects
    for row in conn.execute("SELECT slug, display_name FROM report_objects").fetchall():
        slug = row["slug"]
        dn = _normalize(row["display_name"])
        if dn:
            index[dn] = slug
        # Also index the slug itself (underscores → spaces)
        index[_normalize(slug.replace("_", " "))] = slug

    # From report_object_aliases
    for row in conn.execute(
        "SELECT report_object_slug, alias_value FROM report_object_aliases"
    ).fetchall():
        slug = row["report_object_slug"]
        av = _normalize(row["alias_value"])
        if av:
            index[av] = slug

    return index


def _find_slug(index: dict[str, str], canonical_name: str, aliases_str: str) -> str | None:
    """Try to match canonical_name or any alias to a slug."""
    candidates = [canonical_name] + [a.strip() for a in aliases_str.split("|") if a.strip()]
    for candidate in candidates:
        norm = _normalize(candidate)
        if norm in index:
            return index[norm]
    return None


def _format_address(ulice: str, misto: str, psc: str) -> str:
    parts = [p.strip() for p in [ulice, psc + " " + misto if psc or misto else ""] if p.strip()]
    return ", ".join(parts)


def _format_bank_account(ucet: str, kod_banky: str) -> str:
    ucet = ucet.strip()
    kod = kod_banky.strip()
    if ucet and kod:
        return f"{ucet}/{kod}"
    return ucet or ""


def main():
    dry_run = "--dry-run" in sys.argv

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    index = _build_slug_index(conn)

    with open(TSV_PATH, encoding="utf-8") as f:
        lines = [line for line in f if not line.startswith("#")]
    reader = csv.DictReader(lines, delimiter="\t")
    rows = list(reader)

    matched = 0
    unmatched = 0
    skipped_rentero = 0
    inserted = 0

    for row in rows:
        category = (row.get("category") or "").strip().lower()
        canonical = (row.get("canonical_name") or "").strip()
        aliases_str = row.get("aliases") or ""
        owner = (row.get("owner_name") or "").strip()

        slug = _find_slug(index, canonical, aliases_str)

        if slug is None:
            unmatched += 1
            print(f"  UNMATCHED: {canonical!r}  aliases={aliases_str!r}")
            continue

        matched += 1

        # Skip rentero-owned properties (no external client)
        if not owner or owner.lower() == "rentero property s.r.o.":
            skipped_rentero += 1
            continue

        ico = (row.get("ico") or "").strip()
        dic = (row.get("dic") or "").strip()
        platce_dph = int(row.get("platce_dph") or 0)
        ulice = (row.get("ulice") or "").strip()
        misto = (row.get("misto") or "").strip()
        psc = (row.get("psc") or "").strip()
        adresa = _format_address(ulice, misto, psc)
        bank_account = _format_bank_account(
            row.get("ucet") or "", row.get("kod_banky") or ""
        )

        print(f"  {slug:35s} <- {canonical!r}  owner={owner!r}  ico={ico}")

        if not dry_run:
            conn.execute(
                """INSERT INTO clients
                   (property_slug, name, ico, dic, platce_dph, adresa,
                    bank_account, email, phone, notes, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '', '', '', ?)
                   ON CONFLICT(property_slug) DO UPDATE SET
                     name=excluded.name, ico=excluded.ico, dic=excluded.dic,
                     platce_dph=excluded.platce_dph, adresa=excluded.adresa,
                     bank_account=excluded.bank_account,
                     updated_at=excluded.updated_at""",
                (slug, owner, ico, dic, platce_dph, adresa, bank_account, _now()),
            )
            inserted += 1

    if not dry_run:
        conn.commit()

    print()
    print(f"Matched:  {matched}")
    print(f"  - Rentero (skipped): {skipped_rentero}")
    print(f"  - Clients inserted:  {inserted}")
    print(f"Unmatched: {unmatched}")
    if dry_run:
        print("(DRY RUN — nothing written to DB)")

    conn.close()


if __name__ == "__main__":
    main()
