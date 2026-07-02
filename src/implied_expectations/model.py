"""Forward valuation model: a textbook two-stage FCFF discounted cash flow.

This is the function the solver inverts. Conventions, all deliberate and
documented in the README:

  Explicit period, years t = 1..N:
    revenue_t      = revenue_0 * (1 + g)^t
    NOPAT_t        = revenue_t * margin * (1 - tax_rate)
    reinvestment_t = NOPAT_t * max(0, g / roic)
    FCFF_t         = NOPAT_t - reinvestment_t

  Terminal value at the end of year N (Gordon growth):
    NOPAT_{N+1} = revenue_N * (1 + g_t) * margin * (1 - tax_rate)
    FCFF_{N+1}  = NOPAT_{N+1} * (1 - g_t / RONIC)
    TV_N        = FCFF_{N+1} / (r - g_t)

  Enterprise value = sum of discounted explicit FCFF + discounted TV_N.
  Equity value     = enterprise value - total debt + cash.

Reinvestment is tied to growth through incremental ROIC (reinvestment rate =
g / ROIC), the standard growth-funding identity. RONIC in the terminal period
defaults to the discount rate, which makes growth beyond year N value-neutral:
a conservative, standard choice that keeps the terminal value from smuggling
in extra assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Assumptions:
    """Held parameters. Every default is documented in the README."""

    discount_rate: float = 0.095  # risk-free 4.5% + 5% equity risk premium
    tax_rate: float = 0.21  # US statutory; replaced by the filing's effective rate when available
    roic: float = 0.20  # incremental return on invested capital during the explicit period
    terminal_growth: float = 0.025  # roughly long-run nominal GDP
    terminal_ronic: float | None = None  # None means "equal to discount_rate" (value-neutral)

    def resolved_ronic(self) -> float:
        return self.discount_rate if self.terminal_ronic is None else self.terminal_ronic

    def validate(self) -> None:
        if self.discount_rate <= self.terminal_growth:
            raise ValueError(
                f"discount_rate ({self.discount_rate:.3f}) must exceed terminal_growth "
                f"({self.terminal_growth:.3f}) or the terminal value does not converge"
            )
        if self.roic <= 0:
            raise ValueError("roic must be positive")
        if not 0 <= self.tax_rate < 1:
            raise ValueError("tax_rate must be in [0, 1)")
        if self.resolved_ronic() <= self.terminal_growth:
            raise ValueError("terminal RONIC must exceed terminal_growth")


@dataclass(frozen=True)
class Company:
    """A snapshot of the fundamentals the model needs. Currency units must agree."""

    ticker: str
    revenue: float  # trailing fiscal-year revenue
    operating_income: float  # trailing fiscal-year operating income
    total_debt: float
    cash: float  # cash, equivalents, and short-term investments
    shares: float  # diluted weighted-average shares outstanding

    @property
    def operating_margin(self) -> float:
        if self.revenue <= 0:
            raise ValueError(f"{self.ticker}: revenue must be positive")
        return self.operating_income / self.revenue

    @property
    def net_debt(self) -> float:
        return self.total_debt - self.cash


@dataclass(frozen=True)
class Valuation:
    enterprise_value: float
    pv_explicit: float
    pv_terminal: float


def enterprise_value(
    revenue: float,
    growth: float,
    years: int,
    margin: float,
    assumptions: Assumptions,
) -> Valuation:
    """Value the operating business under one (growth, years, margin) scenario.

    years=0 collapses to a pure Gordon perpetuity on current revenue.
    """
    assumptions.validate()
    if revenue <= 0:
        raise ValueError("revenue must be positive")
    if years < 0:
        raise ValueError("years must be >= 0")
    if growth <= -1:
        raise ValueError("growth must be > -100%")

    a = assumptions
    after_tax = 1.0 - a.tax_rate
    # Reinvestment cannot be negative: a shrinking business does not manufacture
    # extra free cash flow out of the growth identity.
    reinvest_rate = max(0.0, growth / a.roic)

    pv_explicit = 0.0
    rev_t = revenue
    for t in range(1, years + 1):
        rev_t = revenue * (1.0 + growth) ** t
        nopat = rev_t * margin * after_tax
        fcff = nopat * (1.0 - reinvest_rate)
        pv_explicit += fcff / (1.0 + a.discount_rate) ** t

    terminal_reinvest = max(0.0, a.terminal_growth / a.resolved_ronic())
    nopat_next = rev_t * (1.0 + a.terminal_growth) * margin * after_tax
    fcff_next = nopat_next * (1.0 - terminal_reinvest)
    tv = fcff_next / (a.discount_rate - a.terminal_growth)
    pv_terminal = tv / (1.0 + a.discount_rate) ** years

    return Valuation(
        enterprise_value=pv_explicit + pv_terminal,
        pv_explicit=pv_explicit,
        pv_terminal=pv_terminal,
    )


DEFAULT_ASSUMPTIONS = Assumptions()


def value_per_share(
    company: Company,
    growth: float,
    years: int,
    margin: float | None = None,
    assumptions: Assumptions = DEFAULT_ASSUMPTIONS,
) -> float:
    """Equity value per share under one scenario. margin=None uses the current margin."""
    if company.shares <= 0:
        raise ValueError(f"{company.ticker}: shares must be positive")
    m = company.operating_margin if margin is None else margin
    ev = enterprise_value(company.revenue, growth, years, m, assumptions).enterprise_value
    equity = ev - company.total_debt + company.cash
    return equity / company.shares


def with_discount_rate(assumptions: Assumptions, discount_rate: float) -> Assumptions:
    return replace(assumptions, discount_rate=discount_rate)
