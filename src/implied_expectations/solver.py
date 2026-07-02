"""Inversion: solve the forward model backwards from a market price.

Three questions, each answered by root-finding on the forward model:

  implied_growth   what growth rate, held for N years at a given margin,
                   makes the model value equal today's price?
  implied_duration how many years must growth run at a given rate to
                   reach today's price?
  implied_margin   what operating margin, at a given growth and horizon,
                   does the price require?

The solver never fabricates a number. If no growth rate under the cap reaches
the price, it re-expresses the bet as duration at the cap. If even fifty years
at the cap falls short, it says so.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .model import DEFAULT_ASSUMPTIONS, Assumptions, Company, value_per_share

GROWTH_FLOOR = -0.90
MAX_YEARS = 50
_TOL = 1e-10
_MAX_ITER = 200


class SolveMode(enum.Enum):
    GROWTH = "growth"  # a growth rate under the cap reaches the price
    DURATION_AT_CAP = "duration_at_cap"  # price needs the capped growth held longer than N years
    BEYOND_HORIZON = "beyond_horizon"  # even MAX_YEARS at the cap falls short
    BELOW_FLOOR = "below_floor"  # price sits below even a -90%/yr decline scenario


@dataclass(frozen=True)
class GrowthSolution:
    mode: SolveMode
    growth: float | None  # annual revenue growth (GROWTH and DURATION_AT_CAP modes)
    years: float | None  # horizon (fractional in DURATION_AT_CAP mode)
    margin: float  # the margin the solve was run at


def _bisect(f, lo: float, hi: float) -> float:
    """Root of f on [lo, hi]; requires f(lo) and f(hi) to have opposite signs."""
    f_lo = f(lo)
    f_hi = f(hi)
    if f_lo == 0:
        return lo
    if f_hi == 0:
        return hi
    if f_lo * f_hi > 0:
        raise ValueError("root not bracketed")
    for _ in range(_MAX_ITER):
        mid = (lo + hi) / 2.0
        f_mid = f(mid)
        if f_mid == 0 or (hi - lo) / 2.0 < _TOL:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2.0


def implied_growth(
    company: Company,
    price: float,
    years: int = 10,
    margin: float | None = None,
    assumptions: Assumptions = DEFAULT_ASSUMPTIONS,
    growth_cap: float = 0.60,
) -> GrowthSolution:
    """The growth rate the price implies, held for `years` at `margin`.

    Falls back to duration-at-cap when no rate under the cap reaches the price.
    """
    if price <= 0:
        raise ValueError("price must be positive")
    m = company.operating_margin if margin is None else margin
    if m <= 0:
        raise ValueError(
            f"{company.ticker}: operating margin is not positive; the operating-income "
            "inversion is undefined for a loss-making business. Pass an assumed margin "
            "explicitly if you want to model one."
        )

    def gap(g: float) -> float:
        return value_per_share(company, g, years, m, assumptions) - price

    if gap(GROWTH_FLOOR) >= 0:
        # Even a near-total collapse scenario values above the price.
        return GrowthSolution(mode=SolveMode.BELOW_FLOOR, growth=None, years=float(years), margin=m)
    if gap(growth_cap) < 0:
        return implied_duration(company, price, growth_cap, m, assumptions, min_years=years)
    g = _bisect(gap, GROWTH_FLOOR, growth_cap)
    return GrowthSolution(mode=SolveMode.GROWTH, growth=g, years=float(years), margin=m)


def implied_duration(
    company: Company,
    price: float,
    growth: float,
    margin: float | None = None,
    assumptions: Assumptions = DEFAULT_ASSUMPTIONS,
    min_years: int = 0,
) -> GrowthSolution:
    """How many years `growth` must be sustained to reach the price.

    The model steps in whole years; the reported duration interpolates linearly
    between the two bracketing years, which is why it can read "5.6 years".
    """
    if price <= 0:
        raise ValueError("price must be positive")
    m = company.operating_margin if margin is None else margin
    if m <= 0:
        raise ValueError(f"{company.ticker}: margin must be positive for a duration solve")

    prev_value = value_per_share(company, growth, max(min_years, 0), m, assumptions)
    if prev_value >= price and min_years == 0:
        return GrowthSolution(mode=SolveMode.GROWTH, growth=growth, years=0.0, margin=m)
    for n in range(max(min_years, 0) + 1, MAX_YEARS + 1):
        v = value_per_share(company, growth, n, m, assumptions)
        if v >= price:
            # Interpolate the fractional year inside [n-1, n].
            frac = (price - prev_value) / (v - prev_value) if v > prev_value else 1.0
            return GrowthSolution(
                mode=SolveMode.DURATION_AT_CAP,
                growth=growth,
                years=(n - 1) + frac,
                margin=m,
            )
        prev_value = v
    return GrowthSolution(mode=SolveMode.BEYOND_HORIZON, growth=growth, years=None, margin=m)


def implied_margin(
    company: Company,
    price: float,
    growth: float,
    years: int = 10,
    assumptions: Assumptions = DEFAULT_ASSUMPTIONS,
) -> float | None:
    """The operating margin the price requires at a given growth and horizon.

    Enterprise value is linear in margin (margin scales NOPAT and nothing
    else), so this is exact division, not iteration. Returns None when the
    price sits at or below net cash per share, where no margin is implied.
    """
    if price <= 0:
        raise ValueError("price must be positive")
    target_ev = price * company.shares + company.total_debt - company.cash
    if target_ev <= 0:
        return None
    ev_at_unit_margin = (
        value_per_share(company, growth, years, 1.0, assumptions) * company.shares
        + company.total_debt
        - company.cash
    )
    return target_ev / ev_at_unit_margin


def sensitivity_grid(
    company: Company,
    price: float,
    years_options: tuple[int, ...] = (5, 10, 15),
    rate_offsets: tuple[float, ...] = (-0.02, -0.01, 0.0, 0.01, 0.02),
    margin: float | None = None,
    assumptions: Assumptions = DEFAULT_ASSUMPTIONS,
    growth_cap: float = 0.60,
) -> list[list[GrowthSolution]]:
    """Implied growth across horizons (rows) and discount-rate shifts (columns)."""
    from .model import with_discount_rate

    grid: list[list[GrowthSolution]] = []
    for n in years_options:
        row = []
        for dr in rate_offsets:
            a = with_discount_rate(assumptions, assumptions.discount_rate + dr)
            row.append(implied_growth(company, price, n, margin, a, growth_cap))
        grid.append(row)
    return grid
