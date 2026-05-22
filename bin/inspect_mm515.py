"""Quick inspect of mm515 leden 2025 accounting entries to debug Srovnani regression."""
import sqlite3, sys, os
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

c = sqlite3.connect("/home/rentero/rentero/cache/rentero.db")
c.row_factory = sqlite3.Row

for ucet, label in [("315001", "Airbnb"), ("315002", "Booking")]:
    print(f"=== mm515 leden 2025 entries ({label} {ucet}) ===")
    total = 0.0
    for r in c.execute(
        "SELECT id, doc, doc_type, datum, popis, castka, mesic FROM accounting_entries "
        "WHERE stredisko='M515' AND substr(datum,1,7)='2025-01' AND ucet=? "
        "ORDER BY datum, id",
        (ucet,),
    ).fetchall():
        total += r["castka"] or 0
        popis = (r["popis"] or "")[:55]
        print(f"  {r['doc']:>12s} {r['doc_type']:>4s} {r['datum']} {r['castka']:>12.2f}  mesic={r['mesic']}  {popis}")
    print(f"  TOTAL {label}: {total:.2f}")
    print()

# Now show what payout side computes
from report.accounting import build_payout_aggregate
agg_a = build_payout_aggregate(c, "airbnb", 2025, 1)
agg_b = build_payout_aggregate(c, "booking", 2025, 1)
print("=== payout aggregate for 2025-01 ===")
for k, v in sorted(agg_a.items()):
    if "515" in k[0] or "mozart 515" in k[0]:
        print(f"  airbnb {k}: {v:.2f}")
for k, v in sorted(agg_b.items()):
    if "515" in k[0] or "mozart 515" in k[0]:
        print(f"  booking {k}: {v:.2f}")
