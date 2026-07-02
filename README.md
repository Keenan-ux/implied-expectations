# implied-expectations

[![tests](https://github.com/Keenan-ux/implied-expectations/actions/workflows/ci.yml/badge.svg)](https://github.com/Keenan-ux/implied-expectations/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/implied-expectations.svg)](https://pypi.org/project/implied-expectations/)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Every stock price is a forecast. This tool reads the forecast back to you.

Give it a ticker and today's price. It pulls the company's numbers from SEC
EDGAR and runs a discounted cash flow backwards: instead of guessing what the
stock is worth, it solves for the revenue growth rate, the years it must be
sustained, and the operating margin that today's price already assumes. This
is the "expectations investing" method described by Alfred Rappaport and
Michael Mauboussin. The output is never a fair value, a price target, or a
rating. It is the bet the price implies, stated plainly, so you can judge
whether the bet is sane.

```
pip install implied-expectations
```

## Sixty seconds to a first read

The SEC requires every EDGAR client to identify itself with contact info.
Set it once:

```
export EDGAR_USER_AGENT="Your Name you@example.com"
```

Then ask what a price implies. Price is always your input; the tool never
fetches quotes.

```
implied-expectations NVDA --price 180
```

```
NVIDIA CORP (NVDA)  |  price $180.00  |  fiscal year ended 2026-01-25

Fundamentals (SEC EDGAR companyfacts, trailing fiscal year)
  revenue                $215.9B   [Revenues]
  operating income       $130.4B   (60.4% margin)
  tax rate                 15.1%   (effective, from the filing)
  total debt              $12.8B   [LongTermDebtNoncurrent + ...]
  cash + short-term       $62.4B   [CashAndCashEquivalentsAtCarryingValue + ...]
  diluted shares          24.51B   [WeightedAverageNumberOfDilutedSharesOutstanding]
  revenue growth, trailing 3 fiscal years: +100.5%/yr

Held assumptions (every one is a flag)
  discount rate             9.5%
  horizon                  10 yrs
  incremental ROIC           76%   (estimated from the filing: NOPAT / invested capital)
  terminal growth           2.5%   (terminal return on capital = discount rate)

What the price implies
  1. revenue growth of +19.9%/yr for 10 years at a 60.4% margin
  2. or: growth of +20.0%/yr sustained for 10.0 years
  3. or: at +100.5%/yr growth (its trailing pace), a durable operating margin of 0.6%

Implied growth across horizons and discount rates
              7.5%     8.5%     9.5%    10.5%    11.5%
    5 yr    +28.9%   +33.5%   +37.9%   +42.1%   +46.3%
   10 yr    +15.3%   +17.7%   +19.9%   +22.2%   +24.3%
   15 yr    +11.1%   +12.9%   +14.7%   +16.4%   +18.0%
```

Read it like this: at $180, the market is paying about 33.5 times NVIDIA's
trailing operating income. For that to work out at a 9.5% discount rate, the
company needs to grow revenue about 20% a year for a decade while holding a
60% operating margin. Whether that is reasonable is your call. The tool's job
is to make the bet visible.

## The model

A standard two-stage free-cash-flow-to-firm DCF, run in reverse.

For each year of the explicit period, revenue grows at a constant rate g and
earns the operating margin. Operating profit is taxed at the filing's
effective rate. Growth has to be paid for: each year the company reinvests
g / ROIC of its after-tax operating profit, the textbook growth-funding
identity. What remains is free cash flow, discounted at a flat rate.

After the explicit period, growth drops to a terminal rate and the return on
new capital drops to the discount rate, which makes terminal growth
value-neutral. That is deliberate. The terminal value should not smuggle in a
second growth story.

Enterprise value is the sum of both parts. Subtract debt, add cash, divide by
diluted shares, and you have a price. The solver inverts that function three
ways:

| Question | Held fixed | Solved |
| --- | --- | --- |
| What growth does the price imply? | margin, horizon | growth rate |
| How long must growth run? | margin, growth rate | years |
| What margin does the price need? | growth rate, horizon | margin |

The solver never fabricates a number. If no growth rate under the cap reaches
the price, it re-expresses the bet as years-at-the-cap. If fifty years at the
cap still falls short, it says so. If the price sits below what a company
would be worth in a near-total-collapse scenario, it says that too.

## Every assumption, and where it comes from

| Parameter | Default | Source |
| --- | --- | --- |
| price | none, required | you |
| revenue, operating income | trailing fiscal year | the 10-K, via EDGAR companyfacts |
| operating margin | current margin, held flat | computed from the filing |
| tax rate | effective rate from the filing | falls back to 21% when the filing rate is unusable |
| debt, cash | latest reported balance | the filing; leases included where tagged |
| shares | diluted weighted average | the filing |
| incremental ROIC | NOPAT / invested capital, clamped to 10-100% | the filing; falls back to 20% |
| discount rate | 9.5% | 4.5% risk-free plus a 5% equity premium; override with `--discount-rate` |
| horizon | 10 years | convention; override with `--years` |
| terminal growth | 2.5% | roughly long-run nominal GDP |
| terminal return on capital | equals the discount rate | makes terminal growth value-neutral |

Every row is a CLI flag. Change any of them and the tool re-solves.

## What this tool does not do

Honesty about limits beats hedging, so here is the list.

- **It does not judge plausibility.** It tells you the price implies 20%
  growth for a decade. It does not tell you how rare that is.
- **The discount rate is flat.** One rate for every company unless you
  override it. Computing a per-company rate needs price history, and this
  tool deliberately has no price feed.
- **Margins are held flat** over the explicit period. No fade, no S-curve.
- **Loss-makers are refused,** not mispriced. A negative operating margin
  makes the inversion undefined; pass `--margin` to model an assumed one.
- **Banks and insurers are not supported.** Operating income is the wrong
  lens for a balance-sheet business, and the tool says so rather than
  emitting a number.
- **Fundamentals are trailing.** The trailing fiscal year from the last
  annual filing, not a forward estimate.
- **US filers only,** since the data source is SEC XBRL.

## Python API

```python
from implied_expectations import Assumptions, EdgarClient, implied_growth

client = EdgarClient(user_agent="Your Name you@example.com")
f = client.fundamentals("AAPL")

a = Assumptions(tax_rate=f.tax_rate, roic=f.roic)
sol = implied_growth(f.to_company(), price=270.00, years=10, assumptions=a)
print(sol.mode, sol.growth)   # SolveMode.GROWTH 0.1859...
```

`Fundamentals` carries every extracted number plus the XBRL concept it came
from, so nothing is a black box.

## Comparing against boothcheck

[boothcheck.com](https://boothcheck.com) runs the same class of inversion
with more machinery: a discount rate computed per company, segment-level
resolution where filings support it, and mid-cycle normalization for
cyclicals. Its decomposition for ~1,950 US stocks is precomputed and free to
query. Add `--compare` to any run and the tool prints boothcheck's read next
to your local solve:

```
implied-expectations NVDA --price 180 --compare
```

The two will not match exactly. They hold different assumptions, and the gap
between them is itself informative. The comparison calls boothcheck's public
MCP endpoint; nothing is sent except the ticker, and the flag is off by
default.

## Data conduct

EDGAR requests carry your User-Agent, run single-threaded at most 4 per
second, and cache on disk (a day for filings, a week for the ticker map), so
repeat runs make no network calls at all.

## Correctness

The test suite is the point of this repo. It contains hand-computed
closed-form cases (the expected numbers were worked out on paper, not
generated by the code under test), round-trip property tests (value forward
at a known growth rate, invert, recover it to 1e-8), a cross-check of the
year-by-year loop against an independent geometric-sum implementation, and
golden tests over frozen NVIDIA and Apple filings verified against the
reported figures. If you find a case where the solver is wrong, an issue with
the numbers would be very welcome.

```
pip install -e ".[dev]"
pytest
```

## Disclaimer

For informational and research purposes only. Nothing here is investment
advice, a recommendation, or an offer to buy or sell any security. The output
describes what a price implies under stated assumptions; it does not predict
returns. Do your own research.

MIT license.
