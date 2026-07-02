"""Fundamentals from SEC EDGAR companyfacts.

Design rules:
  - The SEC requires a User-Agent that identifies you with contact info.
    This module refuses to run without one (EDGAR_USER_AGENT env var or
    constructor argument). Example: "Jane Doe jane@example.com".
  - Requests are throttled to at most 4 per second, well inside the SEC's
    published 10 req/s fair-access limit, and cached on disk so repeat runs
    make zero network calls.
  - Price is never fetched. Price is a user input.

Extraction takes the latest full fiscal year for flow items (revenue,
operating income, taxes, share count) and the latest reported instant for
balance-sheet items (debt, cash). Every extracted number carries the XBRL
concept it came from, so the CLI can show its provenance.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from .model import Company

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

_MIN_REQUEST_INTERVAL = 0.25  # seconds; 4 req/s ceiling
_TICKERS_TTL = timedelta(days=7)
_FACTS_TTL = timedelta(days=1)

ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F")

REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
PRETAX_CONCEPTS = (
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
)
DEBT_CONCEPTS = (
    "LongTermDebtNoncurrent",
    "LongTermDebtCurrent",
    "OperatingLeaseLiabilityNoncurrent",
    "OperatingLeaseLiabilityCurrent",
    "FinanceLeaseLiabilityNoncurrent",
    "FinanceLeaseLiabilityCurrent",
)
EQUITY_CONCEPTS = (
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
)

# The incremental-ROIC estimate is clamped here. Below 10% the growth-funding
# identity would say growth destroys cash faster than any plausible business;
# above 100% reinvestment is effectively free and the exact figure stops
# mattering. Both bounds are stated in the README.
ROIC_CLAMP = (0.10, 1.00)


class EdgarError(RuntimeError):
    pass


@dataclass(frozen=True)
class Fundamentals:
    ticker: str
    cik: int
    entity_name: str
    fiscal_year_end: str  # ISO date of the trailing fiscal year
    revenue: float
    operating_income: float
    tax_rate: float
    tax_rate_source: str  # "effective" or "default"
    total_debt: float
    cash: float
    shares: float
    roic: float  # NOPAT / invested capital, clamped; or the 0.20 default
    roic_source: str  # "filing" or "default"
    revenue_history: tuple[tuple[str, float], ...]  # (fiscal-year-end, revenue), ascending
    provenance: dict[str, str] = field(default_factory=dict)

    @property
    def operating_margin(self) -> float:
        return self.operating_income / self.revenue

    def revenue_cagr(self, years: int = 3) -> float | None:
        """Trailing revenue CAGR over up to `years` years of filed history."""
        if len(self.revenue_history) < 2:
            return None
        usable = self.revenue_history[-(years + 1) :]
        (start_end, start_val), (last_end, last_val) = usable[0], usable[-1]
        span = (date.fromisoformat(last_end) - date.fromisoformat(start_end)).days / 365.25
        if span < 0.5 or start_val <= 0 or last_val <= 0:
            return None
        return (last_val / start_val) ** (1.0 / span) - 1.0

    def to_company(self) -> Company:
        return Company(
            ticker=self.ticker,
            revenue=self.revenue,
            operating_income=self.operating_income,
            total_debt=self.total_debt,
            cash=self.cash,
            shares=self.shares,
        )


def _default_cache_dir() -> Path:
    override = os.environ.get("IMPLIED_EXPECTATIONS_CACHE")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "implied-expectations"


class EdgarClient:
    def __init__(self, user_agent: str | None = None, cache_dir: Path | None = None):
        ua = user_agent or os.environ.get("EDGAR_USER_AGENT")
        if not ua or "@" not in ua:
            raise EdgarError(
                "SEC EDGAR requires a User-Agent identifying you with contact info, "
                'e.g. "Jane Doe jane@example.com". Set the EDGAR_USER_AGENT environment '
                "variable or pass user_agent= to EdgarClient."
            )
        self.user_agent = ua
        self.cache_dir = cache_dir or _default_cache_dir()
        self._last_request = 0.0

    # -- transport ---------------------------------------------------------

    def _fetch_json(self, url: str, cache_name: str, ttl: timedelta) -> dict:
        cache_path = self.cache_dir / cache_name
        if cache_path.exists():
            age = datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
            if age < ttl:
                return json.loads(cache_path.read_text(encoding="utf-8"))
        wait = _MIN_REQUEST_INTERVAL - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        resp = httpx.get(
            url,
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=30.0,
            follow_redirects=True,
        )
        if resp.status_code == 404:
            raise EdgarError(f"EDGAR returned 404 for {url}")
        resp.raise_for_status()
        data = resp.json()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        return data

    # -- lookups -----------------------------------------------------------

    def cik_for_ticker(self, ticker: str) -> int:
        tickers = self._fetch_json(TICKERS_URL, "company_tickers.json", _TICKERS_TTL)
        wanted = ticker.upper().replace(".", "-")
        for row in tickers.values():
            if row["ticker"].upper() == wanted:
                return int(row["cik_str"])
        raise EdgarError(f"ticker {ticker!r} not found in the SEC ticker map")

    def companyfacts(self, cik: int) -> dict:
        return self._fetch_json(
            COMPANYFACTS_URL.format(cik=cik), f"companyfacts-{cik:010d}.json", _FACTS_TTL
        )

    def fundamentals(self, ticker: str) -> Fundamentals:
        cik = self.cik_for_ticker(ticker)
        facts = self.companyfacts(cik)
        return extract_fundamentals(ticker.upper(), cik, facts)


# -- extraction (pure functions over the companyfacts JSON) -----------------


def _usd_entries(facts: dict, concept: str, taxonomy: str = "us-gaap") -> list[dict]:
    node = facts.get("facts", {}).get(taxonomy, {}).get(concept)
    if not node:
        return []
    units = node.get("units", {})
    for unit_name in ("USD", "shares"):
        if unit_name in units:
            return units[unit_name]
    return []


def _is_annual(entry: dict) -> bool:
    if entry.get("form") not in ANNUAL_FORMS or entry.get("fp") != "FY":
        return False
    start, end = entry.get("start"), entry.get("end")
    if not start or not end:
        return False
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return 340 <= span <= 380


def _annual_series(facts: dict, concept: str) -> list[tuple[str, float]]:
    """All annual values for a concept, deduped by fiscal-year-end (latest filing wins)."""
    by_end: dict[str, tuple[str, float]] = {}
    for e in _usd_entries(facts, concept):
        if not _is_annual(e) or e.get("val") is None:
            continue
        end, filed = e["end"], e.get("filed", "")
        if end not in by_end or filed > by_end[end][0]:
            by_end[end] = (filed, float(e["val"]))
    return [(end, val) for end, (_, val) in sorted(by_end.items())]


def _latest_annual(facts: dict, concepts: tuple[str, ...]) -> tuple[str, str, float] | None:
    """(concept, fiscal-year-end, value) with the most recent year across concepts."""
    best: tuple[str, str, float] | None = None
    for concept in concepts:
        series = _annual_series(facts, concept)
        if series and (best is None or series[-1][0] > best[1]):
            best = (concept, series[-1][0], series[-1][1])
    return best


def _latest_instant(facts: dict, concept: str, taxonomy: str = "us-gaap") -> tuple[str, float] | None:
    best: tuple[str, str, float] | None = None  # (end, filed, val)
    for e in _usd_entries(facts, concept, taxonomy):
        if e.get("start") or e.get("val") is None or not e.get("end"):
            continue  # instants have no start
        key = (e["end"], e.get("filed", ""), float(e["val"]))
        if best is None or key[:2] > best[:2]:
            best = key
    return (best[0], best[2]) if best else None


def extract_fundamentals(ticker: str, cik: int, facts: dict) -> Fundamentals:
    provenance: dict[str, str] = {}

    rev = _latest_annual(facts, REVENUE_CONCEPTS)
    if rev is None:
        raise EdgarError(f"{ticker}: no annual revenue found in companyfacts")
    rev_concept, fy_end, revenue = rev
    provenance["revenue"] = rev_concept
    if revenue <= 0:
        raise EdgarError(f"{ticker}: latest annual revenue is not positive")

    op = _latest_annual(facts, ("OperatingIncomeLoss",))
    if op is None:
        raise EdgarError(
            f"{ticker}: no OperatingIncomeLoss in companyfacts. Banks, insurers, and "
            "some foreign filers do not report it; this tool does not support them."
        )
    _, op_end, operating_income = op
    provenance["operating_income"] = "OperatingIncomeLoss"
    if op_end != fy_end:
        raise EdgarError(
            f"{ticker}: revenue ({fy_end}) and operating income ({op_end}) come from "
            "different fiscal years; refusing to mix periods"
        )

    tax_rate, tax_source = 0.21, "default"
    tax = _latest_annual(facts, ("IncomeTaxExpenseBenefit",))
    pretax = _latest_annual(facts, PRETAX_CONCEPTS)
    if tax and pretax and tax[1] == fy_end and pretax[1] == fy_end and pretax[2] > 0:
        effective = tax[2] / pretax[2]
        if 0.0 <= effective <= 0.45:
            tax_rate, tax_source = effective, "effective"
            provenance["tax"] = f"IncomeTaxExpenseBenefit / {pretax[0]}"

    total_debt, debt_tags = _sum_instants(facts, DEBT_CONCEPTS)
    if total_debt == 0.0:
        lt = _latest_instant(facts, "LongTermDebt")
        if lt:
            total_debt, debt_tags = lt[1], ["LongTermDebt"]
    provenance["debt"] = " + ".join(debt_tags) if debt_tags else "none found (0)"

    cash_val, cash_tags = _sum_instants(facts, ("CashAndCashEquivalentsAtCarryingValue",))
    sti = _latest_instant(facts, "ShortTermInvestments") or _latest_instant(
        facts, "MarketableSecuritiesCurrent"
    )
    if sti:
        cash_val += sti[1]
        has_sti = _latest_instant(facts, "ShortTermInvestments") is not None
        cash_tags.append("ShortTermInvestments" if has_sti else "MarketableSecuritiesCurrent")
    provenance["cash"] = " + ".join(cash_tags) if cash_tags else "none found (0)"

    shares = _latest_annual(facts, ("WeightedAverageNumberOfDilutedSharesOutstanding",))
    if shares and shares[1] == fy_end:
        share_count = shares[2]
        provenance["shares"] = "WeightedAverageNumberOfDilutedSharesOutstanding"
    else:
        dei = _latest_instant(facts, "EntityCommonStockSharesOutstanding", taxonomy="dei")
        if dei is None:
            raise EdgarError(f"{ticker}: no usable share count in companyfacts")
        share_count = dei[1]
        provenance["shares"] = "dei:EntityCommonStockSharesOutstanding"

    roic, roic_source = 0.20, "default"
    equity = None
    for concept in EQUITY_CONCEPTS:
        inst = _latest_instant(facts, concept)
        if inst:
            equity = inst[1]
            break
    if equity is not None:
        invested_capital = equity + total_debt - cash_val
        if invested_capital > 0 and operating_income > 0:
            raw = operating_income * (1.0 - tax_rate) / invested_capital
            roic = min(max(raw, ROIC_CLAMP[0]), ROIC_CLAMP[1])
            roic_source = "filing"
            provenance["roic"] = (
                f"OperatingIncomeLoss x (1 - tax) / ({concept} + debt - cash)"
                + (" [clamped]" if raw != roic else "")
            )

    return Fundamentals(
        ticker=ticker,
        cik=cik,
        entity_name=facts.get("entityName", ticker),
        fiscal_year_end=fy_end,
        revenue=revenue,
        operating_income=operating_income,
        tax_rate=tax_rate,
        tax_rate_source=tax_source,
        total_debt=total_debt,
        cash=cash_val,
        shares=share_count,
        roic=roic,
        roic_source=roic_source,
        revenue_history=tuple(_annual_series(facts, rev_concept)),
        provenance=provenance,
    )


def _sum_instants(facts: dict, concepts: tuple[str, ...]) -> tuple[float, list[str]]:
    """Sum the latest instant of each concept, skipping values staler than ~400 days
    vs the freshest one (a discontinued tag must not leak an old balance in)."""
    found: list[tuple[str, str, float]] = []
    for concept in concepts:
        inst = _latest_instant(facts, concept)
        if inst:
            found.append((concept, inst[0], inst[1]))
    if not found:
        return 0.0, []
    newest = max(end for _, end, _ in found)
    cutoff = (date.fromisoformat(newest) - timedelta(days=400)).isoformat()
    kept = [(c, e, v) for c, e, v in found if e >= cutoff]
    return sum(v for _, _, v in kept), [c for c, _, _ in kept]
