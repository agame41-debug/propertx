# DPH za ubytovací služby (12 %) for Rentero-owned objects — Design

**Date:** 2026-05-23
**Status:** Approved (pending spec review)

## Problem

Rentero-owned objects (`client_type == 'rentero'`) are the accommodation **supplier**, so
Rentero owes the Czech reduced-rate accommodation VAT (12 %, *ubytovací služby*) to the
state on the revenue from those objects. The property page already has a
**"Vyúčtování DPH → K odvedení státu"** card ([templates/partials/property_dph_summary.html](../../../templates/partials/property_dph_summary.html)),
but for Rentero objects its output VAT (`vat_output_czk`) is only:

```
vat_output_czk = vat_rentero_fee_czk (= 0 for rentero) + vat_room_prep_czk (úklid+balíčky × 21 %)
```

The single largest component — the 12 % accommodation VAT — is **missing entirely**. For
`Zitna_208_NOVA / 04-2026` the card shows ≈ 1 316 Kč output VAT, while the real
accommodation VAT owed is ≈ 5 141 Kč.

## Formula (verified on prod data)

Per property-month, aggregate over the (deduped, non-excluded) reservation rows:

```
gross_payout_czk         = Σ payout_czk
platform_commission_czk  = Σ provize_czk          # NEW aggregate
city_tax_czk             = Σ city_tax_czk

accommodation_gross_czk  = gross_payout_czk + platform_commission_czk − city_tax_czk
accommodation_vat_czk    = round(accommodation_gross_czk × 0.12 / 1.12, 2)   # VAT-inclusive extraction
```

This is the full accommodation consideration the guest paid (room + cleaning + balíčky +
commission, i.e. everything except the city tax pass-through), with the 12 % VAT extracted
from the VAT-inclusive gross.

**Verification — `Zitna_208_NOVA`, 04/2026:**

| Field | Value |
|---|---|
| Σ payout_czk | 41 991.22 |
| Σ provize_czk | 7 844.83 |
| Σ city_tax_czk | 1 850.00 |
| **accommodation_gross_czk** | **47 986.05** |
| **accommodation_vat_czk** (× 0.12/1.12) | **5 141.36** |

Matches the user's expected ≈ 5 138 Kč (small delta = recollection/rounding).

## Decisions (user-confirmed)

1. **Replace, don't add.** For Rentero objects the output VAT becomes the 12 %
   accommodation VAT, *replacing* the current 21 % "Příprava pokoje" output line. The gross
   already includes úklid + balíčky, so taxing them again at 21 % would double-count.
2. **výstup − vstup.** `accommodation_vat_czk` is the output VAT. The existing input-VAT
   column (deductible DPH from rated expenses) stays. Saldo "K odvedení státu" =
   `vat_output_czk − vat_input_czk`.
3. **Scope = `client_type == 'rentero'` only.** klient / z_klient objects are unchanged —
   their card shows Rentero's *prefakturace* VAT (a different liability; the accommodation
   VAT on those objects is the client's, not Rentero's).

## Architecture

All computation lives in `build_report_summary` ([report/summary.py](../../../report/summary.py)) —
the single source of truth read by the property page, the "Zisk" KPI, **and** the dashboard
aggregates. Computing it anywhere else (route/template) would not reach the dashboard totals
and would duplicate logic. The template only displays.

## Changes

### 1. `report/summary.py`

- Module constant: `ACCOMMODATION_VAT_RATE = 0.12` (Czech reduced rate for ubytovací služby).
- New aggregate: `platform_commission_czk = _r(Σ provize_czk)` (provize_czk is present on
  every CalculatedRow). Add to `result` as `platform_commission_czk`.
- For `client_type == 'rentero'` only, compute and attach:
  - `accommodation_gross_czk = _r(gross_payout_czk + platform_commission_czk − city_tax_czk)`
  - `accommodation_vat_czk = _r(accommodation_gross_czk × ACCOMMODATION_VAT_RATE / (1 + ACCOMMODATION_VAT_RATE))`
- Override the output-VAT alias so it branches on type:
  ```python
  if client_type == "rentero":
      result["vat_output_czk"] = result["accommodation_vat_czk"]
  else:
      result["vat_output_czk"] = result["dph_prefakturace_klient_czk"]
  ```
  `dph_prefakturace_klient_czk` itself is left defined and unchanged (other consumers keep
  working); only the `vat_output_czk` alias diverges for rentero.
- `vat_balance_czk` (already `vat_output − vat_input`, computed after `vat_output`) and
  `zisk_czk` (already `gross_payout − expenses − vat_balance`) pick up the new value
  automatically — no formula edits needed, only ordering must keep the rentero override
  *before* the `vat_balance_czk` line.
- klient / z_klient: `accommodation_gross_czk` / `accommodation_vat_czk` are **not** added to
  `result` (absent), so the template can branch on their presence.

### 2. `templates/partials/property_dph_summary.html`

The headline `DPH na výstupu` value (`summary.vat_output_czk`) is already correct. Only the
breakdown under it changes:

```jinja
<div class="dph-breakdown">
  {% if summary.accommodation_vat_czk is not none %}
    <div class="dph-row">
      <span class="dph-row-k">Ubytovací služby (12 %)</span>
      <span class="dph-row-v">+&nbsp;{{ fmt_czk(summary.accommodation_vat_czk or 0) }}</span>
    </div>
  {% else %}
    {# existing Rentero fee + Příprava pokoje breakdown #}
  {% endif %}
</div>
```

The vstup column and Saldo column are unchanged.

### 3. KPI card — no change

[templates/partials/property_kpi.html](../../../templates/partials/property_kpi.html) shows
"DPH =" (`vat_balance_czk`) and "Zisk" (`zisk_czk`); both update automatically.

## Testing

- **[tests/test_summary.py](../../../tests/test_summary.py):** add `provize_czk` to the
  `_fee_rows()` fixture (or a dedicated fixture) and assert, for a rentero prop:
  `accommodation_gross_czk`, `accommodation_vat_czk == round(gross*0.12/1.12, 2)`,
  `vat_output_czk == accommodation_vat_czk`, `vat_balance_czk == vat_output − vat_input`,
  and that `zisk_czk` reflects the new balance. For klient / z_klient: assert
  `"accommodation_vat_czk" not in s` and `vat_output_czk == dph_prefakturace_klient_czk`.
- **[tests/test_web_generation.py](../../../tests/test_web_generation.py):** render the DPH
  summary partial. With a rentero summary (`accommodation_vat_czk` set) the HTML contains
  "Ubytovací služby" and not "Příprava pokoje"; with a klient summary it still shows the
  legacy "Rentero fee" / "Příprava pokoje" breakdown.

## Out of scope / known limitations

- **Rentero objects tagged `z_klient`** (e.g. Opletalova_10 → Rentero Investments) keep their
  current prefakturace card; the 12 % accommodation treatment is not applied to them. No
  regression — status quo preserved. Flagged for a possible follow-up.
- **Reverse-charge DPH on platform commission** (`dph_provize`, 21 %) is not added to the
  settlement — it nets to zero (self-assessed output + matching input deduction).
- **Excel / accounting exports** do not consume `vat_output_czk` / `vat_balance_czk` /
  `zisk_czk`, so they are unaffected.

## Blast radius

`vat_output_czk` / `vat_balance_czk` / `zisk_czk` for rentero objects change. Affected
surfaces: the property DPH card, the property "Zisk" KPI, and the dashboard aggregates
(`rentero_vat_output_czk` / `rentero_vat_balance_czk` and the per-object Zisk total in
[report/routes/dashboard.py](../../../report/routes/dashboard.py)). The dashboard "K odvedení
státu" total will rise because it previously under-counted Rentero accommodation VAT.
