# Modelová výplata klienta — KPI card for Rentero-owned objects

**Date:** 2026-05-23
**Status:** Design (awaiting implementation)

## Goal

On the property page, Rentero-owned objects (`client_type='rentero'`)
currently show an empty "—" card in KPI slot 3 (the fee slot), because
Rentero charges itself no commission. Replace that empty card with an
**illustrative model**: what the numbers *would* be if this were a client
object — how much a client would be paid (`Výplata klientovi`) and how much
Rentero would earn (`Odměna Rentero`). This is a sales aid for showing a
prospective client the economics of a property.

The model is **purely illustrative**. It does NOT change any real figure:
`Zisk`, `Vyplaceno platformami`, `Výdaje`, and the real `rentero_fee_czk`
(which stays 0 for Rentero-owned objects) are untouched.

## Scope

In scope:
- A `model_client` sub-result computed in `build_report_summary` only for
  `client_type='rentero'`.
- Rendering that model in KPI slot 3 of `templates/partials/property_kpi.html`
  (replacing the muted "—" card added on 2026-05-22).

Out of scope / unchanged:
- `klient` and `z_klient` objects — they keep their real fee card.
- The real summary figures for Rentero-owned objects (fee remains 0).
- Excel export (no model shown there for now).

## Model formula

The model uses the **klient** calculation (15 % of accommodation income),
using the object's stored `rentero_commission` (default 0.15) and `vat_rate`
(default 0.21). Výplata is computed **before** the month's real expenses, so
the figure is a clean, comparable "what you'd earn from the rental" number
that doesn't swing with one month's incidental costs.

```
model_fee          = accommodation_income_czk * rentero_commission_rate   # net odměna
model_vat          = model_fee * vat_rate                                 # DPH on odměna
model_payout       = accommodation_income_czk - model_fee - model_vat     # Výplata klientovi (před výdaji)
model_odmena_total = model_fee + model_vat                                # Odměna Rentero (s DPH)
```

## Data layer — `report/summary.py`

In the existing `elif client_type == "rentero":` branch (added 2026-05-22),
after zeroing the real fee, attach a model sub-dict:

```python
result["model_client"] = {
    "rentero_commission_rate": rentero_commission_rate,   # 0.15
    "rentero_fee_czk": model_fee,                          # net odměna
    "vat_rentero_fee_czk": model_vat,                      # DPH
    "rentero_odmena_total_czk": _r(model_fee + model_vat), # odměna s DPH
    "client_payout_before_expenses_czk": model_payout,     # výplata klientovi
}
```

`model_client` is present **only** for `client_type='rentero'`. For
`klient`/`z_klient` the key is absent, so downstream code can test for it.

## UI layer — `templates/partials/property_kpi.html`

KPI slot 3 currently has three states:
1. `not _is_rentero_owned` → client fee card (`Výplata Rentero …`).
2. `_is_rentero_owned` and fee > 0 → real `Rentero fee` card (z_klient-tagged).
3. `_is_rentero_owned` and fee == 0 → muted "—" card.

Change state 3: if `summary.model_client` exists **and** its
`client_payout_before_expenses_czk` > 0, render the model card; otherwise
keep the muted "—" (e.g. a month with no bookings — nothing to model).

Model card (`kpi kpi-mute`, so it reads as illustrative, not real cash):

```
┌────────────────────────────────┐
│ Modelová výplata klienta        │   ← kpi-label
│    27 166 Kč                    │   ← kpi-value  = model client_payout
│ Odměna Rentero −4 978 (15 %)    │   ← sub: net fee + commission %
│  · DPH −1 045                   │   ← sub: model vat (only if > 0)
└────────────────────────────────┘
```

- Label: **"Modelová výplata klienta"**.
- Value: `model_client.client_payout_before_expenses_czk`.
- Sub pair 1: `Odměna Rentero −{rentero_fee_czk} ({rate} %)`.
- Sub pair 2 (only if `vat_rentero_fee_czk > 0`): `DPH −{vat_rentero_fee_czk}`.

## Testing

`tests/test_summary.py` (or new test module):
- `client_type='rentero'` with accommodation income → `summary["model_client"]`
  present; `client_payout_before_expenses_czk == cena − fee − vat`;
  `rentero_odmena_total_czk == fee + vat`; real `rentero_fee_czk == 0`.
- `client_type='klient'` and `'z_klient'` → no `model_client` key.

`tests/test_web_generation.py` (partial render):
- Rentero-owned object with a non-zero model payout → rendered KPI contains
  "Modelová výplata klienta" and the model figures.
- Rentero-owned object with zero accommodation income → still shows "—",
  no "Modelová výplata klienta".

## Deploy

Same flow as the 2026-05-22 fix: commit own hunks → push → in-place patch of
`report/summary.py` + `templates/partials/property_kpi.html` on prod (with
backup of both files) → `systemctl restart rentero` → verify.
