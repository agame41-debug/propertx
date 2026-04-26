"""Validates expense amount triplet (gross/net/dph) for consistency.

Used by /expenses/add and /expenses/{id}/edit endpoints. Persists canonical values
regardless of what the client sent — the calculator-strip form computes them on the
client for UX, but the server is the source of truth.
"""
from __future__ import annotations

EPSILON_CZK: float = 0.02
ALLOWED_VAT_RATES: tuple[float, ...] = (0.0, 0.12, 0.21)


class ExpenseValidationError(ValueError):
    """Raised when client-supplied gross/net/dph triplet is internally inconsistent."""


def validate_and_canonicalize(
    *,
    gross: float | None,
    net: float | None,
    dph: float | None,
    vat_rate: float | None,
) -> tuple[float, float, float, float]:
    """Returns ``(gross, canonical_net, canonical_dph, vat_rate)`` or raises.

    Canonical formulas:
        canonical_net = round(gross / (1 + vat_rate), 2)
        canonical_dph = round(gross - canonical_net, 2)

    If `net` or `dph` are provided and diverge from the canonical values by more
    than EPSILON_CZK, ExpenseValidationError is raised with a Czech-localized
    message suitable for displaying as a flash error.
    """
    if gross is None or gross <= 0:
        raise ExpenseValidationError("Celková částka (Celkem) musí být větší než 0 Kč.")
    if vat_rate is None or vat_rate not in ALLOWED_VAT_RATES:
        raise ExpenseValidationError(
            f"Sazba DPH musí být jedna z {ALLOWED_VAT_RATES} ({{0%, 12%, 21%}}); dostal: {vat_rate!r}."
        )

    canonical_net = round(gross / (1 + vat_rate), 2)
    canonical_dph = round(gross - canonical_net, 2)

    if net is not None and abs(net - canonical_net) > EPSILON_CZK:
        raise ExpenseValidationError(
            f"Bez DPH ({net:.2f} Kč) neodpovídá Celkem ({gross:.2f} Kč) při sazbě "
            f"{int(vat_rate * 100)}%. Očekáváno: {canonical_net:.2f} Kč."
        )
    if dph is not None and abs(dph - canonical_dph) > EPSILON_CZK:
        raise ExpenseValidationError(
            f"DPH ({dph:.2f} Kč) neodpovídá Celkem ({gross:.2f} Kč) při sazbě "
            f"{int(vat_rate * 100)}%. Očekáváno: {canonical_dph:.2f} Kč."
        )

    return gross, canonical_net, canonical_dph, vat_rate
