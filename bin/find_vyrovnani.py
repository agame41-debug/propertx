"""Find all (slug, year, month) report snapshots that contain a reservation
with a Vyrovnání-type extra batch — these need regeneration so the engine
applies the new split-payout-window subtraction.
"""
import sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8")

c = sqlite3.connect("/home/rentero/rentero/cache/rentero.db")
codes = [
    r[0] for r in c.execute(
        "SELECT DISTINCT confirmation_code FROM payout_batch_items "
        "WHERE item_type = 'Vyrovnání' AND confirmation_code <> ''"
    ).fetchall()
]
print(f"Codes with bare Vyrovnání: {len(codes)}")

slugs_months = set()
for code in codes:
    for r in c.execute(
        "SELECT slug, year, month FROM report_rows WHERE confirmation_code = ?",
        (code,),
    ).fetchall():
        slugs_months.add((r[0], r[1], r[2]))
        # also include the month containing the Vyrovnání payout date (if
        # it's a different month, both must regen)

print(f"slug-month pairs to regen: {len(slugs_months)}")
for sm in sorted(slugs_months):
    print(" ", sm)
