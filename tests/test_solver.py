"""Inversion correctness: round trips, fallbacks, and the margin closed form."""

import pytest

from implied_expectations.model import Assumptions, Company, value_per_share
from implied_expectations.solver import (
    SolveMode,
    implied_duration,
    implied_growth,
    implied_margin,
)

A = Assumptions(discount_rate=0.10, tax_rate=0.25, roic=0.20, terminal_growth=0.02)
C = Company(ticker="TEST", revenue=1000.0, operating_income=200.0, total_debt=100.0, cash=50.0, shares=100.0)


def test_growth_round_trip():
    # Value forward at a known growth, invert, recover it.
    for g in (-0.15, 0.0, 0.07, 0.12, 0.25, 0.45):
        price = value_per_share(C, g, 10, None, A)
        sol = implied_growth(C, price, years=10, assumptions=A)
        assert sol.mode is SolveMode.GROWTH
        assert sol.growth == pytest.approx(g, abs=1e-8)


def test_growth_round_trip_at_explicit_margin():
    price = value_per_share(C, 0.10, 10, 0.35, A)
    sol = implied_growth(C, price, years=10, margin=0.35, assumptions=A)
    assert sol.growth == pytest.approx(0.10, abs=1e-8)
    assert sol.margin == 0.35


def test_duration_round_trip_at_integer_years():
    price = value_per_share(C, 0.20, 7, None, A)
    sol = implied_duration(C, price, growth=0.20, assumptions=A)
    assert sol.mode is SolveMode.DURATION_AT_CAP
    assert sol.years == pytest.approx(7.0, abs=0.02)


def test_duration_interpolates_between_years():
    v7 = value_per_share(C, 0.20, 7, None, A)
    v8 = value_per_share(C, 0.20, 8, None, A)
    sol = implied_duration(C, (v7 + v8) / 2, growth=0.20, assumptions=A)
    assert 7.0 < sol.years < 8.0


def test_growth_falls_back_to_duration_when_cap_is_not_enough():
    # A price above the 10-year value at the cap must re-express as duration.
    price = value_per_share(C, 0.30, 20, None, A)
    sol = implied_growth(C, price, years=10, assumptions=A, growth_cap=0.30)
    assert sol.mode is SolveMode.DURATION_AT_CAP
    assert sol.growth == 0.30
    assert sol.years == pytest.approx(20.0, abs=0.05)


def test_beyond_horizon_reported_honestly():
    price = value_per_share(C, 0.30, 50, None, A) * 2.0
    sol = implied_growth(C, price, years=10, assumptions=A, growth_cap=0.30)
    assert sol.mode is SolveMode.BEYOND_HORIZON
    assert sol.years is None


def test_below_floor_reported_honestly():
    # A company holding far more net cash than any decline scenario can burn:
    # even the -90%/yr collapse values above $1, so no growth is implied.
    cash_rich = Company(
        ticker="VAULT", revenue=1000.0, operating_income=200.0, total_debt=0.0, cash=5000.0, shares=100.0
    )
    sol = implied_growth(cash_rich, 1.0, years=10, assumptions=A)
    assert sol.mode is SolveMode.BELOW_FLOOR
    assert sol.growth is None


def test_margin_closed_form_round_trip():
    price = value_per_share(C, 0.10, 10, 0.25, A)
    m = implied_margin(C, price, growth=0.10, years=10, assumptions=A)
    assert m == pytest.approx(0.25, rel=1e-12)


def test_margin_closed_form_matches_forward_model():
    # Cross-check: plug the implied margin back in and recover the price.
    m = implied_margin(C, 40.0, growth=0.08, years=10, assumptions=A)
    assert value_per_share(C, 0.08, 10, m, A) == pytest.approx(40.0, rel=1e-12)


def test_margin_none_when_price_below_net_cash():
    net_cash_co = Company(
        ticker="CASHBOX", revenue=100.0, operating_income=10.0, total_debt=0.0, cash=5000.0, shares=100.0
    )
    assert implied_margin(net_cash_co, 40.0, growth=0.05, years=10, assumptions=A) is None


def test_loss_maker_refuses_growth_solve():
    loser = Company(ticker="LOSS", revenue=1000.0, operating_income=-50.0, total_debt=0.0, cash=0.0, shares=100.0)
    with pytest.raises(ValueError, match="loss-making"):
        implied_growth(loser, 10.0, assumptions=A)
    # But an explicitly assumed margin makes the solve well-defined.
    sol = implied_growth(loser, 10.0, margin=0.15, assumptions=A)
    assert sol.mode in (SolveMode.GROWTH, SolveMode.BELOW_FLOOR)


def test_price_must_be_positive():
    with pytest.raises(ValueError, match="price"):
        implied_growth(C, -5.0, assumptions=A)
