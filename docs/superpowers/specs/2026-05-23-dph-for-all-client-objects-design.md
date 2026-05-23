# DPH settlement for all client objects (incl. provize) — Design

**Date:** 2026-05-23
**Status:** Approved (pending spec review)

## Problem

The "Vyúčtování DPH" card and the DPH figures on the property page are shown only when
`_is_dph_applicable` (Rentero-owned **or** external client with `platce_dph=1`). But Rentero
is a VAT payer and handles DPH on **every** managed object regardless of the client's VAT
status: it charges 21% VAT on its odměna and on prefakturované costs, reverse-charges the
Airbnb/Booking commission, and deducts input VAT on expenses. So the settlement should be
visible for all objects.

Two distinct gaps:

1. **Display.** Non-payer client objects (e.g. Svornosti 1497/1, client `platce_dph=0`) hide
   the card entirely.
2. **Computation.** For klient/z_klient objects the output VAT today is only
   `dph_prefakturace_klient_czk` (odměna DPH + příprava DPH). It **omits the commission VAT**
   that Rentero recharges to the client. The card therefore understates "K odvedení státu".

## Verified example — Svornosti 1497/1, 04/2026 (client `platce_dph=0`, commission 20%)

Matches the user's bookkeeping (two invoices, total 21% output DPH ≈ 6 754):

| Component | base | DPH 21% |
|---|---|---|
| odměna Rentero | 9 884 | 2 076 |
| příprava pokoje | 9 883 | 2 075 |
| provize Airbnb+Booking | 12 108 | **2 543** |
| *(Netflix recharge — nets out)* | 289 | *61* |

- Current app output VAT: `2 076 + 2 075 = 4 151` (provize missing).
- Target: `odměna + příprava + provize = 6 694` (matching "6 754 minus internet").

## Decisions (user-confirmed)

1. **Output VAT for klient/z_klient = prefakturace + commission VAT + recharged-expense VAT.**
   Recharged expenses appear in **both** output and input (net zero per expense), mirroring the
   bookkeeping. Net saldo = `odměna + příprava + provize`.
2. **Show the settlement for all objects.** The card, the KPI "DPH =" sub-row, and the "Výdaje"
   DPH line render for every object.
3. **Keep the "Plátce DPH" badge accurate.** The badge stays tied to the real client VAT status
   (`_is_dph_applicable`); it is **not** shown on non-payer clients. Only the settlement display
   goes universal.
4. **Rentero-owned objects unchanged** — their output VAT is the 12% accommodation VAT, whose
   base already includes the commission, so provize is not added separately.

## Why commission VAT is net output (reverse charge)

Airbnb (IE) / Booking (NL) invoice Rentero without VAT → Rentero self-assesses 21% output and
deducts the same as input (net zero on that invoice). Separately, Rentero recharges the
commission to the client with 21% output VAT. The self-assessment and its deduction cancel,
leaving the recharge as a **net output** VAT Rentero remits. Hence `+ commission VAT` in output.

## Architecture

All computation stays in `build_report_summary` ([report/summary.py](../../../report/summary.py)) —
the shared source of truth for the property page and the dashboard aggregates. The template
only displays.

## Changes

### 1. `report/summary.py`

- New aggregate: `platform_commission_vat_czk = _r(Σ dph_provize_czk)` (per-row `dph_provize_czk`
  already exists; for Svornosti = 2 543). Add to `result`.
- **Reorder:** compute the `vat_input` block (rated expenses → `vat_input_czk`,
  `vat_input_count`) **before** the output-VAT branch, so the klient branch can reference
  `result["vat_input_czk"]`.
- Output-VAT branch:
  ```python
  if client_type == "rentero":
      ...                                  # 12% accommodation VAT (unchanged)
      result["vat_output_czk"] = accommodation_vat_czk
  else:  # klient / z_klient
      # Rentero recharges costs to the client with output VAT and charges its
      # odměna with VAT. Output VAT = prefakturace (fee + room prep) + commission
      # VAT (net of the Airbnb/Booking reverse charge) + recharged-expense VAT.
      result["vat_output_czk"] = _r(
          result["dph_prefakturace_klient_czk"]
          + platform_commission_vat_czk
          + result["vat_input_czk"]
      )
  ```
- `vat_balance_czk = _r(vat_output − vat_input)` (unchanged line, now after the branch). For
  klient this yields `odměna + příprava + provize` (recharged expenses cancel) — Svornosti = 6 694.
- `zisk_czk` unchanged (None for klient/z_klient; rentero formula untouched).

### 2. Templates — separate badge from settlement

- [templates/property.html](../../../templates/property.html): keep `_is_dph_applicable`
  (drives the badge). Add `{% set _show_dph = True %}` (Rentero always handles DPH). Gate the
  card include on `_show_dph` instead of `_is_dph_applicable`.
- [templates/partials/property_intro.html](../../../templates/partials/property_intro.html):
  "Plátce DPH" badge stays gated on `_is_dph_applicable` (no change).
- [templates/partials/property_kpi.html](../../../templates/partials/property_kpi.html):
  the two `_is_dph_applicable` gates (KPI "DPH =" sub-row; "Výdaje" KPI DPH line) → `_show_dph`.
- [templates/partials/property_expenses.html](../../../templates/partials/property_expenses.html):
  the `_is_dph_applicable` gate on the header DPH meta → `_show_dph`.

### 3. `templates/partials/property_dph_summary.html` — klient breakdown

The Výstup column breakdown for klient/z_klient (the `{% else %}` branch — rentero keeps its
"Ubytovací služby (12 %)" line) gains a commission line and, when there are rated expenses, a
recharged-expenses line, all with the `−` sign (owed, per the current sign convention):

```jinja
        {% else %}
          <div class="dph-row"><span class="dph-row-k">Rentero fee</span>
            <span class="dph-row-v">−&nbsp;{{ fmt_czk(summary.vat_rentero_fee_czk or 0) }}</span></div>
          <div class="dph-row"><span class="dph-row-k">Příprava pokoje</span>
            <span class="dph-row-v">−&nbsp;{{ fmt_czk(summary.vat_room_prep_czk or 0) }}</span></div>
          {% if (summary.platform_commission_vat_czk or 0) > 0 %}
          <div class="dph-row"><span class="dph-row-k">Provize (21&nbsp;%)</span>
            <span class="dph-row-v">−&nbsp;{{ fmt_czk(summary.platform_commission_vat_czk or 0) }}</span></div>
          {% endif %}
          {% if (summary.vat_input_czk or 0) > 0 %}
          <div class="dph-row"><span class="dph-row-k">Přefakturované výdaje</span>
            <span class="dph-row-v">−&nbsp;{{ fmt_czk(summary.vat_input_czk or 0) }}</span></div>
          {% endif %}
        {% endif %}
```

The vstup column and saldo column are unchanged (vstup already shows the expense input VAT;
saldo = výstup − vstup).

## Testing

- **summary** ([tests/test_summary.py](../../../tests/test_summary.py) /
  [tests/test_summary_new_fields.py](../../../tests/test_summary_new_fields.py)):
  - klient with `provize_czk`/`dph_provize_czk` rows: assert
    `platform_commission_vat_czk` summed correctly; `vat_output_czk ==
    dph_prefakturace_klient_czk + platform_commission_vat_czk + vat_input_czk`;
    `vat_balance_czk == dph_prefakturace_klient_czk + platform_commission_vat_czk` (expenses
    cancel) — assert with a rated expense to prove the net-zero.
  - rentero unchanged: `vat_output_czk == accommodation_vat_czk` (existing test stays green).
- **display** ([tests/test_web_generation.py](../../../tests/test_web_generation.py)):
  - DPH summary partial with a klient summary that has `platform_commission_vat_czk > 0` →
    HTML contains "Provize".
  - Full property page (or the existing render path) for a `platce_dph=0` client renders the
    "Vyúčtování DPH" card but **not** the "Plátce DPH" badge.

## Out of scope / notes

- **z_klient** follows the same output formula; its `vat_rentero_fee_czk` is already 0 in the
  current code, so its output = příprava + provize + recharged expenses. No special-casing.
- **Reverse-charge mechanics** are not modelled line-by-line; the net effect (commission VAT as
  output) is what the card shows.
- **Excel / accounting exports** do not consume `vat_output_czk` / `vat_balance_czk`, so they
  are unaffected.

## Blast radius

`vat_output_czk` / `vat_balance_czk` rise for **all** klient/z_klient objects by the commission
VAT. Affected: each klient property's DPH card + "DPH =" KPI sub, and the dashboard aggregates
(`rentero_vat_output_czk` / `rentero_vat_balance_czk` and the total "K odvedení státu"), which
previously under-counted commission VAT. `zisk_czk` is unaffected (None for klient; rentero
formula unchanged).
