"""Regenerate a trimmed companyfacts fixture for the golden tests.

Usage:
    EDGAR_USER_AGENT="you you@example.com" python scripts/make_fixture.py NVDA AAPL

Downloads the full companyfacts document and keeps only the concepts the
extractor reads, so the committed fixture stays small. Golden tests then run
against the frozen file, never the network.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from implied_expectations.edgar import (  # noqa: E402
    DEBT_CONCEPTS,
    EQUITY_CONCEPTS,
    PRETAX_CONCEPTS,
    REVENUE_CONCEPTS,
    EdgarClient,
)

KEEP = {
    "us-gaap": set(REVENUE_CONCEPTS)
    | set(PRETAX_CONCEPTS)
    | set(DEBT_CONCEPTS)
    | set(EQUITY_CONCEPTS)
    | {
        "OperatingIncomeLoss",
        "IncomeTaxExpenseBenefit",
        "LongTermDebt",
        "CashAndCashEquivalentsAtCarryingValue",
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    },
    "dei": {"EntityCommonStockSharesOutstanding"},
}


def trim(facts: dict) -> dict:
    out = {"entityName": facts.get("entityName"), "cik": facts.get("cik"), "facts": {}}
    for taxonomy, wanted in KEEP.items():
        source = facts.get("facts", {}).get(taxonomy, {})
        kept = {k: v for k, v in source.items() if k in wanted}
        if kept:
            out["facts"][taxonomy] = kept
    return out


def main() -> None:
    client = EdgarClient()
    fixtures = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    for ticker in sys.argv[1:]:
        cik = client.cik_for_ticker(ticker)
        trimmed = trim(client.companyfacts(cik))
        path = fixtures / f"companyfacts-{ticker.upper()}.json"
        path.write_text(json.dumps(trimmed, indent=1), encoding="utf-8")
        print(f"{ticker.upper()}: CIK {cik}, wrote {path} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
