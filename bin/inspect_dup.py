"""Inspect duplicate FKV/FHS entries across source files."""
import sqlite3
c = sqlite3.connect("/home/rentero/rentero/cache/rentero.db")
c.row_factory = sqlite3.Row

print("=== source_files (accounting) ===")
for r in c.execute("SELECT id, original_name, is_active, imported_at FROM source_files WHERE source_type='accounting' ORDER BY id").fetchall():
    print(f"  id={r['id']} active={r['is_active']} imported={r['imported_at']} name={r['original_name']!r}")

print()
print("=== mm515 leden FKV/FHS entries by source_file_id ===")
for r in c.execute(
    "SELECT source_file_id, doc, doc_type, datum, castka, popis FROM accounting_entries "
    "WHERE stredisko='M515' AND substr(datum,1,7)='2025-01' AND ucet='315001' "
    "AND doc_type IN ('FKV','FHS') ORDER BY source_file_id, doc, datum"
).fetchall():
    print(f"  src={r['source_file_id']} {r['doc']:>12s} {r['doc_type']} {r['datum']} {r['castka']:>12.2f}")

print()
print("=== count of entries per source_file_id ===")
for r in c.execute(
    "SELECT source_file_id, COUNT(*) AS n, "
    "SUM(CASE WHEN castka<0 THEN 1 ELSE 0 END) AS negs FROM accounting_entries GROUP BY source_file_id"
).fetchall():
    print(f"  src={r['source_file_id']}: total={r['n']} negatives={r['negs']}")

print()
print("=== sample of file 17 entries (popis + castka) ===")
for r in c.execute(
    "SELECT doc, datum, castka, popis FROM accounting_entries WHERE source_file_id=17 LIMIT 12"
).fetchall():
    print(f"  {r['doc']:>12s} {r['datum']} {r['castka']:>12.2f}  {r['popis'][:60]}")
