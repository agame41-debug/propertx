"""Inspect HMH2FKHMJ5 (Daniel Klesc) and his correction."""
import sqlite3, json, sys
sys.stdout.reconfigure(encoding="utf-8")
c = sqlite3.connect("/home/rentero/rentero/cache/rentero.db")
c.row_factory = sqlite3.Row

print("=== HMH2FKHMJ5* in report_rows ===")
for r in c.execute(
    "SELECT slug, year, month, confirmation_code, data "
    "FROM report_rows WHERE confirmation_code LIKE 'HMH2FKHMJ5%'"
).fetchall():
    d = json.loads(r["data"])
    code = r["confirmation_code"]
    print(f"\n  {r['slug']} {r['year']}-{r['month']:02d}  code={code}")
    print(f"    guest={d.get('guest_name')!r}  src={d.get('source')!r}")
    print(f"    payout_czk={d.get('payout_czk')}  cena={d.get('cena_ubytovani_czk')}")
    print(f"    effective_payout_eur={d.get('effective_payout_eur')}  payout_eur={d.get('payout_eur')}  czk_booked={d.get('czk_booked')}")
    print(f"    is_payout_adjustment={d.get('is_payout_adjustment')}  is_excluded={d.get('is_excluded')}")
    print(f"    batch_ref={d.get('batch_ref')}  parent={d.get('adjustment_parent_code')}")
    print(f"    adjustment_original_year={d.get('adjustment_original_year')} month={d.get('adjustment_original_month')}")

print("\n=== payout_batch_items for HMH2FKHMJ5 ===")
for r in c.execute(
    "SELECT batch_ref, item_index, item_type, amount_eur, amount_czk "
    "FROM payout_batch_items WHERE confirmation_code='HMH2FKHMJ5' ORDER BY batch_ref"
).fetchall():
    print(" ", dict(r))

print("\n=== payout_batches for those refs ===")
for r in c.execute(
    "SELECT batch_ref, payout_date, amount_eur, amount_czk, implied_rate "
    "FROM payout_batches WHERE batch_ref IN "
    "(SELECT batch_ref FROM payout_batch_items WHERE confirmation_code='HMH2FKHMJ5')"
).fetchall():
    print(" ", dict(r))

print("\n=== override_events for HMH2FKHMJ5 ===")
for r in c.execute(
    "SELECT id, slug, year, month, scope_id, field, old_value, new_value, is_active "
    "FROM override_events WHERE scope_id LIKE 'HMH2FKHMJ5%'"
).fetchall():
    print(" ", dict(r))

print("\n=== hostify_reservations for HMH2FKHMJ5 ===")
for r in c.execute(
    "SELECT confirmation_code, guest_name, source, status, listing_nickname, "
    "check_in, check_out, assigned_year, assigned_month "
    "FROM hostify_reservations WHERE confirmation_code='HMH2FKHMJ5'"
).fetchall():
    print(" ", dict(r))

print("\n=== all_batches_map for code (via hostify payload aircover/refund?) ===")
# Search Airbnb payout source files for the ref appearing alongside 'řešení'
for r in c.execute(
    "SELECT batch_ref, item_index, item_type, amount_eur, confirmation_code, guest_name "
    "FROM payout_batch_items WHERE batch_ref IN "
    "(SELECT batch_ref FROM payout_batch_items WHERE confirmation_code='HMH2FKHMJ5') "
    "ORDER BY batch_ref, item_index"
).fetchall():
    print(" ", dict(r))
