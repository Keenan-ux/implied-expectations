"""Command-line interface.

    implied-expectations NVDA --price 172.50

Prints the fundamentals it pulled, the assumptions it held, and the bet the
price implies. Never a fair value, never a rating.
"""

from __future__ import annotations

import argparse
import json
import sys

from .edgar import EdgarClient, EdgarError, Fundamentals
from .model import Assumptions
from .solver import GrowthSolution, SolveMode, implied_duration, implied_growth, implied_margin, sensitivity_grid

DISCLAIMER = (
    "This is what the price implies under these assumptions, not a fair value\n"
    "and not a prediction. For informational and research purposes only; not\n"
    "investment advice."
)


def _money(x: float) -> str:
    for scale, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= scale:
            return f"${x / scale:,.1f}{suffix}"
    return f"${x:,.0f}"


def _count(x: float) -> str:
    for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= scale:
            return f"{x / scale:,.2f}{suffix}"
    return f"{x:,.0f}"


def _describe(sol: GrowthSolution) -> str:
    if sol.mode is SolveMode.GROWTH:
        return f"{sol.growth:+.1%}/yr for {sol.years:.0f} years"
    if sol.mode is SolveMode.DURATION_AT_CAP:
        return f"{sol.growth:+.1%}/yr sustained for {sol.years:.1f} years"
    if sol.mode is SolveMode.BEYOND_HORIZON:
        return f"more than {sol.growth:+.0%}/yr for 50 years (beyond the model's horizon)"
    return "less than a -90%/yr collapse (price sits below the floor scenario)"


def _grid_cell(sol: GrowthSolution) -> str:
    if sol.mode is SolveMode.GROWTH:
        return f"{sol.growth:+.1%}"
    if sol.mode is SolveMode.DURATION_AT_CAP:
        return f">{sol.growth:.0%}"  # implied growth exceeds the cap at this horizon
    if sol.mode is SolveMode.BEYOND_HORIZON:
        return "beyond"
    return "below"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="implied-expectations",
        description="Invert a stock price into the growth, duration, and margin it implies.",
    )
    p.add_argument("ticker", help="US ticker, e.g. NVDA")
    p.add_argument("--price", type=float, required=True, help="current share price (user input; never fetched)")
    p.add_argument("--years", type=int, default=10, help="explicit forecast horizon (default 10)")
    p.add_argument("--discount-rate", type=float, default=None, help="override the discount rate")
    p.add_argument("--risk-free", type=float, default=0.045, help="risk-free rate (default 0.045)")
    p.add_argument("--erp", type=float, default=0.05, help="equity risk premium (default 0.05)")
    p.add_argument(
        "--roic", type=float, default=None,
        help="incremental ROIC (default: estimated from the filing as NOPAT / invested capital, clamped to 10-100%%)",
    )
    p.add_argument("--terminal-growth", type=float, default=0.025, help="perpetuity growth (default 0.025)")
    p.add_argument("--margin", type=float, default=None, help="operating margin to hold (default: current)")
    p.add_argument("--tax", type=float, default=None, help="tax rate (default: effective rate from the filing)")
    p.add_argument(
        "--growth-cap", type=float, default=0.60,
        help="above this, the bet is expressed as duration (default 0.60)",
    )
    p.add_argument(
        "--duration-growth", type=float, default=0.20,
        help="growth rate for the duration view (default 0.20)",
    )
    p.add_argument(
        "--margin-growth", type=float, default=None,
        help="growth rate for the implied-margin view (default: trailing 3y revenue CAGR)",
    )
    p.add_argument(
        "--user-agent", default=None,
        help='SEC User-Agent, e.g. "Jane Doe jane@example.com" (or set EDGAR_USER_AGENT)',
    )
    p.add_argument(
        "--compare", action="store_true",
        help="also fetch boothcheck.com's precomputed read for comparison",
    )
    p.add_argument("--json", action="store_true", help="machine-readable output")
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        client = EdgarClient(user_agent=args.user_agent)
        f = client.fundamentals(args.ticker)
    except EdgarError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    discount = args.discount_rate if args.discount_rate is not None else args.risk_free + args.erp
    assumptions = Assumptions(
        discount_rate=discount,
        tax_rate=args.tax if args.tax is not None else f.tax_rate,
        roic=args.roic if args.roic is not None else f.roic,
        terminal_growth=args.terminal_growth,
    )
    company = f.to_company()
    margin = args.margin  # None -> current

    growth_sol = implied_growth(company, args.price, args.years, margin, assumptions, args.growth_cap)
    duration_sol = implied_duration(company, args.price, args.duration_growth, margin, assumptions)
    cagr = f.revenue_cagr(3)
    margin_growth = args.margin_growth if args.margin_growth is not None else cagr
    margin_sol = (
        implied_margin(company, args.price, margin_growth, args.years, assumptions)
        if margin_growth is not None
        else None
    )
    grid = sensitivity_grid(
        company, args.price, (5, 10, 15), (-0.02, -0.01, 0.0, 0.01, 0.02), margin, assumptions, args.growth_cap
    )

    if args.json:
        out = _to_json(f, args.price, assumptions, growth_sol, duration_sol, margin_growth, margin_sol)
        print(json.dumps(out, indent=2))
        return 0

    _print_report(f, args, assumptions, growth_sol, duration_sol, cagr, margin_growth, margin_sol, grid)

    if args.compare:
        _print_comparison(args.ticker)
    return 0


def _to_json(f: Fundamentals, price, a: Assumptions, growth_sol, duration_sol, margin_growth, margin_sol) -> dict:
    return {
        "ticker": f.ticker,
        "entity": f.entity_name,
        "price": price,
        "fiscalYearEnd": f.fiscal_year_end,
        "fundamentals": {
            "revenue": f.revenue,
            "operatingIncome": f.operating_income,
            "operatingMargin": f.operating_margin,
            "taxRate": f.tax_rate,
            "taxRateSource": f.tax_rate_source,
            "totalDebt": f.total_debt,
            "cash": f.cash,
            "shares": f.shares,
            "roic": f.roic,
            "roicSource": f.roic_source,
            "provenance": f.provenance,
        },
        "assumptions": {
            "discountRate": a.discount_rate,
            "roic": a.roic,
            "terminalGrowth": a.terminal_growth,
            "taxRate": a.tax_rate,
        },
        "implied": {
            "growth": {
                "mode": growth_sol.mode.value,
                "growth": growth_sol.growth,
                "years": growth_sol.years,
                "margin": growth_sol.margin,
            },
            "duration": {"mode": duration_sol.mode.value, "growth": duration_sol.growth, "years": duration_sol.years},
            "margin": {"atGrowth": margin_growth, "margin": margin_sol},
        },
    }


def _print_report(
    f: Fundamentals, args, a: Assumptions, growth_sol, duration_sol, cagr, margin_growth, margin_sol, grid
) -> None:
    # ASCII only: Windows consoles on legacy codepages garble anything fancier.
    print(f"\n{f.entity_name} ({f.ticker})  |  price ${args.price:,.2f}  |  fiscal year ended {f.fiscal_year_end}\n")

    print("Fundamentals (SEC EDGAR companyfacts, trailing fiscal year)")
    print(f"  revenue             {_money(f.revenue):>10}   [{f.provenance['revenue']}]")
    print(f"  operating income    {_money(f.operating_income):>10}   ({f.operating_margin:.1%} margin)")
    tax_note = "effective, from the filing" if f.tax_rate_source == "effective" else "default; filing rate unavailable"
    print(f"  tax rate            {f.tax_rate:>10.1%}   ({tax_note})")
    print(f"  total debt          {_money(f.total_debt):>10}   [{f.provenance['debt']}]")
    print(f"  cash + short-term   {_money(f.cash):>10}   [{f.provenance['cash']}]")
    print(f"  diluted shares      {_count(f.shares):>10}   [{f.provenance['shares']}]")
    if cagr is not None:
        print(f"  revenue growth, trailing 3 fiscal years: {cagr:+.1%}/yr")

    print("\nHeld assumptions (every one is a flag)")
    print(f"  discount rate       {a.discount_rate:>10.1%}")
    print(f"  horizon             {args.years:>7} yrs")
    roic_note = (
        "estimated from the filing: NOPAT / invested capital"
        if args.roic is None and f.roic_source == "filing"
        else ("default; filing estimate unavailable" if args.roic is None else "your flag")
    )
    print(f"  incremental ROIC    {a.roic:>10.0%}   ({roic_note})")
    print(f"  terminal growth     {a.terminal_growth:>10.1%}   (terminal return on capital = discount rate)")

    held_margin = growth_sol.margin
    print("\nWhat the price implies")
    print(f"  1. revenue growth of {_describe(growth_sol)} at a {held_margin:.1%} margin")
    print(f"  2. or: growth of {_describe(duration_sol)}")
    if margin_sol is not None and margin_growth is not None:
        src = "its trailing pace" if args.margin_growth is None else "your rate"
        if margin_sol > 1.0:
            print(
                f"  3. or: at {margin_growth:+.1%}/yr growth ({src}), the price needs a margin above 100%"
                " of revenue. No margin gets there; the price is betting on more growth than that."
            )
        else:
            print(f"  3. or: at {margin_growth:+.1%}/yr growth ({src}), a durable operating margin of {margin_sol:.1%}")

    print("\nImplied growth across horizons and discount rates")
    offsets = (-0.02, -0.01, 0.0, 0.01, 0.02)
    header = "         " + "".join(f"{a.discount_rate + off:>9.1%}" for off in offsets)
    print(header)
    for years, row in zip((5, 10, 15), grid, strict=True):
        print(f"  {years:>3} yr " + "".join(f"{_grid_cell(sol):>9}" for sol in row))

    print(f"\n{DISCLAIMER}")


def _print_comparison(ticker: str) -> None:
    from .boothcheck import whats_priced_in

    print("\nboothcheck.com's precomputed read (computed discount rate, more machinery):")
    try:
        read = whats_priced_in(ticker)
        for line in read.summary.splitlines():
            print(f"  {line}")
    except Exception as e:  # noqa: BLE001 - the comparison is best-effort, never fatal
        print(f"  unavailable ({e})")


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
