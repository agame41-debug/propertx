import pytest
from report.expenses_validation import (
    validate_and_canonicalize,
    ExpenseValidationError,
    EPSILON_CZK,
    ALLOWED_VAT_RATES,
)


class TestValidateAndCanonicalize:
    def test_gross_only_with_21_percent(self):
        gross, net, dph, rate = validate_and_canonicalize(gross=121.0, net=None, dph=None, vat_rate=0.21)
        assert gross == 121.0
        assert net == 100.0
        assert dph == 21.0
        assert rate == 0.21

    def test_gross_only_with_zero_rate(self):
        gross, net, dph, rate = validate_and_canonicalize(gross=500.0, net=None, dph=None, vat_rate=0.0)
        assert (gross, net, dph, rate) == (500.0, 500.0, 0.0, 0.0)

    def test_gross_only_with_12_percent(self):
        gross, net, dph, rate = validate_and_canonicalize(gross=112.0, net=None, dph=None, vat_rate=0.12)
        assert gross == 112.0
        assert net == 100.0
        assert dph == 12.0
        assert rate == 0.12

    def test_all_three_consistent(self):
        gross, net, dph, rate = validate_and_canonicalize(gross=121.0, net=100.0, dph=21.0, vat_rate=0.21)
        assert (gross, net, dph, rate) == (121.0, 100.0, 21.0, 0.21)

    def test_net_within_epsilon_passes(self):
        # 100.01 vs canonical 100.00 — within 0.02 tolerance
        gross, net, dph, rate = validate_and_canonicalize(gross=121.0, net=100.01, dph=21.0, vat_rate=0.21)
        # Persisted values are canonical, not user input
        assert net == 100.0

    def test_net_outside_epsilon_raises(self):
        with pytest.raises(ExpenseValidationError, match="Bez DPH"):
            validate_and_canonicalize(gross=121.0, net=99.0, dph=21.0, vat_rate=0.21)

    def test_dph_outside_epsilon_raises(self):
        with pytest.raises(ExpenseValidationError, match="DPH"):
            validate_and_canonicalize(gross=121.0, net=100.0, dph=15.0, vat_rate=0.21)

    def test_zero_gross_raises(self):
        with pytest.raises(ExpenseValidationError, match="větší než 0"):
            validate_and_canonicalize(gross=0.0, net=None, dph=None, vat_rate=0.21)

    def test_negative_gross_raises(self):
        with pytest.raises(ExpenseValidationError, match="větší než 0"):
            validate_and_canonicalize(gross=-50.0, net=None, dph=None, vat_rate=0.21)

    def test_none_gross_raises(self):
        with pytest.raises(ExpenseValidationError, match="větší než 0"):
            validate_and_canonicalize(gross=None, net=None, dph=None, vat_rate=0.21)

    def test_none_rate_raises(self):
        with pytest.raises(ExpenseValidationError, match="Sazba DPH"):
            validate_and_canonicalize(gross=100.0, net=None, dph=None, vat_rate=None)

    def test_invalid_rate_raises(self):
        with pytest.raises(ExpenseValidationError, match="Sazba DPH"):
            validate_and_canonicalize(gross=100.0, net=None, dph=None, vat_rate=0.15)

    def test_rounding_boundary_100_kc_at_21(self):
        # Real-world rounding edge case
        gross, net, dph, rate = validate_and_canonicalize(gross=100.0, net=None, dph=None, vat_rate=0.21)
        assert net == 82.64
        assert dph == 17.36

    def test_constants_exposed(self):
        assert EPSILON_CZK == 0.02
        assert ALLOWED_VAT_RATES == (0.0, 0.12, 0.21)
