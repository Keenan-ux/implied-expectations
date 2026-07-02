"""Forward-model correctness against hand-computed closed forms.

Every expected number here was computed by hand (arithmetic in the comments),
not by running the model. If these fail, the model is wrong, not the test.
"""

import pytest

from implied_expectations.model import Assumptions, Company, enterprise_value, value_per_share

# Clean-number assumptions used throughout: r=10%, tax=25%, ROIC=20%, g_t=2%,
# terminal RONIC = r = 10%.
A = Assumptions(discount_rate=0.10, tax_rate=0.25, roic=0.20, terminal_growth=0.02)


def test_single_year_hand_computed():
    # revenue0=1000, g=10%, N=1, margin=20%:
    #   rev1   = 1100
    #   NOPAT1 = 1100 * 0.20 * 0.75 = 165
    #   reinvestment rate = g/ROIC = 0.10/0.20 = 0.5 -> FCFF1 = 82.5
    #   PV(explicit) = 82.5 / 1.1 = 75.0 exactly
    # terminal:
    #   NOPAT2 = 1100 * 1.02 * 0.20 * 0.75 = 168.3
    #   terminal reinvestment = g_t/RONIC = 0.02/0.10 = 0.2 -> FCFF2 = 134.64
    #   TV = 134.64 / (0.10 - 0.02) = 1683.0
    #   PV(TV) = 1683.0 / 1.1 = 1530.0 exactly
    # EV = 75.0 + 1530.0 = 1605.0
    v = enterprise_value(revenue=1000.0, growth=0.10, years=1, margin=0.20, assumptions=A)
    assert v.pv_explicit == pytest.approx(75.0, abs=1e-9)
    assert v.pv_terminal == pytest.approx(1530.0, abs=1e-9)
    assert v.enterprise_value == pytest.approx(1605.0, abs=1e-9)


def test_zero_years_is_pure_gordon():
    # N=0: value the current revenue as a perpetuity.
    #   NOPAT1 = 1000 * 1.02 * 0.20 * 0.75 = 153
    #   FCFF1  = 153 * (1 - 0.02/0.10) = 122.4
    #   EV     = 122.4 / 0.08 = 1530.0
    v = enterprise_value(revenue=1000.0, growth=0.10, years=0, margin=0.20, assumptions=A)
    assert v.pv_explicit == 0.0
    assert v.enterprise_value == pytest.approx(1530.0, abs=1e-9)


def test_zero_growth_is_an_annuity_plus_terminal():
    # g=0: no reinvestment, FCFF = NOPAT = 1000*0.20*0.75 = 150 every year.
    # 5-year annuity at 10% plus the Gordon terminal discounted 5 years.
    annuity = 150.0 * (1 - 1.1**-5) / 0.10
    terminal = (1000.0 * 1.02 * 0.20 * 0.75 * (1 - 0.2)) / 0.08 / 1.1**5
    v = enterprise_value(revenue=1000.0, growth=0.0, years=5, margin=0.20, assumptions=A)
    assert v.pv_explicit == pytest.approx(annuity, rel=1e-12)
    assert v.pv_terminal == pytest.approx(terminal, rel=1e-12)


def test_declining_revenue_does_not_manufacture_cash():
    # Negative growth clamps reinvestment at zero; FCFF must equal NOPAT,
    # never exceed it.
    v = enterprise_value(revenue=1000.0, growth=-0.10, years=3, margin=0.20, assumptions=A)
    year1_fcff_pv = (1000.0 * 0.9 * 0.20 * 0.75) / 1.1
    assert v.pv_explicit < 3 * 150.0  # strictly less than flat NOPAT would give
    assert v.pv_explicit > year1_fcff_pv  # but years 2-3 still contribute


def test_value_is_linear_in_margin():
    # Margin scales NOPAT and nothing else, so EV(m) = m * EV(1).
    at_one = enterprise_value(1000.0, 0.08, 10, 1.0, A).enterprise_value
    at_quarter = enterprise_value(1000.0, 0.08, 10, 0.25, A).enterprise_value
    assert at_quarter == pytest.approx(0.25 * at_one, rel=1e-12)


def test_value_is_increasing_in_growth_years_margin():
    prev = None
    for g in [-0.20, -0.10, 0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.60]:
        v = enterprise_value(1000.0, g, 10, 0.20, A).enterprise_value
        if prev is not None:
            assert v > prev
        prev = v
    prev = None
    for n in range(0, 30, 3):
        v = enterprise_value(1000.0, 0.15, n, 0.20, A).enterprise_value
        if prev is not None:
            assert v > prev
        prev = v


def test_terminal_ronic_equal_to_discount_rate_collapses_to_nopat_over_r():
    # With RONIC = r the (1 - g_t/r) haircut exactly cancels the (r - g_t)
    # denominator: TV = NOPAT_{N+1} * (r-g_t)/r / (r-g_t) = NOPAT_{N+1} / r.
    # Growth beyond the horizon adds no value; only the one-step (1+g_t)
    # factor inside NOPAT_{N+1} remains.
    for gt in (0.01, 0.025, 0.04):
        a = Assumptions(discount_rate=0.10, tax_rate=0.25, roic=0.20, terminal_growth=gt)
        v = enterprise_value(1000.0, 0.0, 0, 0.20, a).enterprise_value
        nopat_next = 1000.0 * (1 + gt) * 0.20 * 0.75
        assert v == pytest.approx(nopat_next / 0.10, rel=1e-12)


def test_guardrails():
    with pytest.raises(ValueError, match="discount_rate"):
        Assumptions(discount_rate=0.02, terminal_growth=0.025).validate()
    with pytest.raises(ValueError, match="roic"):
        Assumptions(roic=0.0).validate()
    with pytest.raises(ValueError, match="revenue"):
        enterprise_value(0.0, 0.1, 10, 0.2, A)
    with pytest.raises(ValueError, match="growth"):
        enterprise_value(1000.0, -1.0, 10, 0.2, A)


def test_value_per_share_bridge():
    # equity = EV - debt + cash; per-share divides by shares.
    c = Company(ticker="TEST", revenue=1000.0, operating_income=200.0, total_debt=100.0, cash=50.0, shares=100.0)
    ev = enterprise_value(1000.0, 0.10, 1, 0.20, A).enterprise_value  # 1605.0
    assert value_per_share(c, 0.10, 1, None, A) == pytest.approx((1605.0 - 100.0 + 50.0) / 100.0)
    assert c.operating_margin == pytest.approx(0.20)
    assert c.net_debt == pytest.approx(50.0)
    assert ev == pytest.approx(1605.0)
