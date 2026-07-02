"""Golden tests: the full pipeline over frozen real-world filings.

The fixtures are trimmed SEC companyfacts documents for NVIDIA (fiscal 2026,
ended 2026-01-25) and Apple (fiscal 2025, ended 2025-09-27), captured with
scripts/make_fixture.py. The prices are fixed example inputs, not quotes.

The pinned solve values were cross-checked against an independent closed-form
implementation (geometric sums instead of the model's year-by-year loop); the
two agreed to under 1e-9 relative across a 120-scenario grid. The extraction
values were verified by hand against the filings: NVDA fiscal-2026 revenue of
$215.938B at a 60.4% operating margin, AAPL fiscal-2025 revenue of $416.161B
at 32.0%, both as reported.

If you regenerate the fixtures after new filings land, these numbers change.
That is expected; re-verify and re-pin.
"""

import json
from pathlib import Path

import pytest

from implied_expectations.edgar import extract_fundamentals
from implied_expectations.model import Assumptions, value_per_share
from implied_expectations.solver import SolveMode, implied_duration, implied_growth, implied_margin

FIXTURES = Path(__file__).parent / "fixtures"


def load(ticker: str):
    facts = json.loads((FIXTURES / f"companyfacts-{ticker}.json").read_text(encoding="utf-8"))
    return extract_fundamentals(ticker, 0, facts)


def test_nvda_extraction():
    f = load("NVDA")
    assert f.entity_name == "NVIDIA CORP"
    assert f.fiscal_year_end == "2026-01-25"
    assert f.revenue == 215_938_000_000.0
    assert f.operating_income == 130_387_000_000.0
    assert f.operating_margin == pytest.approx(0.6038, abs=1e-4)
    assert f.tax_rate == pytest.approx(0.15117, abs=1e-5)
    assert f.tax_rate_source == "effective"
    assert f.total_debt == 12_814_000_000.0
    assert f.cash == 62_359_000_000.0
    assert f.shares == 24_514_000_000.0
    assert f.roic == pytest.approx(0.758426, abs=1e-6)
    assert f.roic_source == "filing"
    # 26.97B -> 215.94B over three fiscal years: it doubled every year.
    assert f.revenue_cagr(3) == pytest.approx(1.0052, abs=1e-4)


def test_aapl_extraction():
    f = load("AAPL")
    assert f.entity_name == "Apple Inc."
    assert f.fiscal_year_end == "2025-09-27"
    assert f.revenue == 416_161_000_000.0
    assert f.operating_income == 133_050_000_000.0
    assert f.operating_margin == pytest.approx(0.3197, abs=1e-4)
    assert f.tax_rate == pytest.approx(0.15610, abs=1e-5)
    assert f.total_debt == 96_434_000_000.0
    assert f.cash == 68_507_000_000.0
    assert f.shares == 15_004_697_000.0
    assert f.roic == pytest.approx(0.835311, abs=1e-6)
    assert f.revenue_cagr(3) == pytest.approx(0.0181, abs=1e-4)


def test_nvda_solve_at_180():
    # $180 x 24.514B shares + net debt = a $4,363B enterprise value,
    # 33.5x trailing operating income.
    f = load("NVDA")
    a = Assumptions(tax_rate=f.tax_rate, roic=f.roic)
    c = f.to_company()

    sol = implied_growth(c, 180.00, years=10, assumptions=a)
    assert sol.mode is SolveMode.GROWTH
    assert sol.growth == pytest.approx(0.1994566, abs=1e-6)
    # Round trip: the implied growth must reproduce the price exactly.
    assert value_per_share(c, sol.growth, 10, None, a) == pytest.approx(180.00, abs=1e-6)

    dur = implied_duration(c, 180.00, growth=0.20, assumptions=a)
    assert dur.mode is SolveMode.DURATION_AT_CAP
    assert dur.years == pytest.approx(9.9654, abs=1e-3)


def test_aapl_solve_at_270():
    # $270 x 15.005B shares + net debt = a $4,079B enterprise value,
    # 30.7x trailing operating income.
    f = load("AAPL")
    a = Assumptions(tax_rate=f.tax_rate, roic=f.roic)
    c = f.to_company()

    sol = implied_growth(c, 270.00, years=10, assumptions=a)
    assert sol.mode is SolveMode.GROWTH
    assert sol.growth == pytest.approx(0.1859199, abs=1e-6)
    assert value_per_share(c, sol.growth, 10, None, a) == pytest.approx(270.00, abs=1e-6)

    # At Apple's trailing ~1.8% revenue pace, the price implies a margin near
    # 100% of revenue: the honest reading is that the price is a growth bet.
    m = implied_margin(c, 270.00, growth=f.revenue_cagr(3), years=10, assumptions=a)
    assert m == pytest.approx(0.97557, abs=1e-4)
