"""Extraction correctness over a synthetic companyfacts document. No network."""

import pytest

from implied_expectations.edgar import EdgarClient, EdgarError, extract_fundamentals


def _annual(start, end, val, filed, form="10-K"):
    return {"start": start, "end": end, "val": val, "fp": "FY", "form": form, "filed": filed}


def _instant(end, val, filed="2025-03-01", form="10-K"):
    return {"end": end, "val": val, "fp": "FY", "form": form, "filed": filed}


FACTS = {
    "entityName": "Test Corp",
    "facts": {
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [_instant("2025-01-31", 105.0)]}
            }
        },
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        _annual("2022-02-01", "2023-01-31", 800.0, "2023-03-01"),
                        _annual("2023-02-01", "2024-01-31", 900.0, "2024-03-01"),
                        # restated in a later filing; must win over the 900
                        _annual("2023-02-01", "2024-01-31", 910.0, "2025-03-01"),
                        _annual("2024-02-01", "2025-01-31", 1000.0, "2025-03-01"),
                        # a quarterly row that must be ignored
                        {
                            "start": "2024-11-01", "end": "2025-01-31", "val": 260.0,
                            "fp": "Q4", "form": "10-Q", "filed": "2025-02-15",
                        },
                    ]
                }
            },
            "OperatingIncomeLoss": {
                "units": {"USD": [_annual("2024-02-01", "2025-01-31", 200.0, "2025-03-01")]}
            },
            "IncomeTaxExpenseBenefit": {
                "units": {"USD": [_annual("2024-02-01", "2025-01-31", 30.0, "2025-03-01")]}
            },
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": {
                "units": {"USD": [_annual("2024-02-01", "2025-01-31", 200.0, "2025-03-01")]}
            },
            "LongTermDebtNoncurrent": {"units": {"USD": [_instant("2025-01-31", 80.0)]}},
            "LongTermDebtCurrent": {"units": {"USD": [_instant("2025-01-31", 20.0)]}},
            # discontinued tag with an ancient balance; the staleness guard must drop it
            "OperatingLeaseLiabilityNoncurrent": {
                "units": {"USD": [_instant("2020-01-31", 500.0, filed="2020-03-01")]}
            },
            "StockholdersEquity": {"units": {"USD": [_instant("2025-01-31", 400.0)]}},
            "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [_instant("2025-01-31", 150.0)]}},
            "ShortTermInvestments": {"units": {"USD": [_instant("2025-01-31", 50.0)]}},
            "WeightedAverageNumberOfDilutedSharesOutstanding": {
                "units": {"shares": [_annual("2024-02-01", "2025-01-31", 100.0, "2025-03-01")]}
            },
        },
    },
}


def test_extraction():
    f = extract_fundamentals("TEST", 12345, FACTS)
    assert f.entity_name == "Test Corp"
    assert f.fiscal_year_end == "2025-01-31"
    assert f.revenue == 1000.0
    assert f.operating_income == 200.0
    assert f.operating_margin == pytest.approx(0.20)
    assert f.tax_rate == pytest.approx(0.15)  # 30 / 200
    assert f.tax_rate_source == "effective"
    assert f.total_debt == 100.0  # 80 + 20; the 2020 lease balance excluded as stale
    assert f.cash == 200.0  # 150 + 50
    assert f.shares == 100.0  # diluted weighted average preferred over dei's 105
    # ROIC = NOPAT / invested capital = 200*(1-0.15) / (400 + 100 - 200) = 170/300
    assert f.roic == pytest.approx(170.0 / 300.0)
    assert f.roic_source == "filing"


def test_restatement_wins_and_quarterlies_ignored():
    f = extract_fundamentals("TEST", 12345, FACTS)
    assert f.revenue_history == (
        ("2023-01-31", 800.0),
        ("2024-01-31", 910.0),
        ("2025-01-31", 1000.0),
    )


def test_revenue_cagr():
    f = extract_fundamentals("TEST", 12345, FACTS)
    # 800 -> 1000 over two fiscal years (731 days).
    cagr = f.revenue_cagr(3)
    assert cagr == pytest.approx((1000.0 / 800.0) ** (365.25 / 731) - 1, rel=1e-6)


def test_mixed_fiscal_years_refused():
    import copy

    facts = copy.deepcopy(FACTS)
    facts["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"] = [
        _annual("2023-02-01", "2024-01-31", 180.0, "2024-03-01")
    ]
    with pytest.raises(EdgarError, match="different fiscal years"):
        extract_fundamentals("TEST", 12345, facts)


def test_missing_operating_income_explains_itself():
    import copy

    facts = copy.deepcopy(FACTS)
    del facts["facts"]["us-gaap"]["OperatingIncomeLoss"]
    with pytest.raises(EdgarError, match="does not support"):
        extract_fundamentals("TEST", 12345, facts)


def test_user_agent_required(monkeypatch):
    monkeypatch.delenv("EDGAR_USER_AGENT", raising=False)
    with pytest.raises(EdgarError, match="User-Agent"):
        EdgarClient()
    with pytest.raises(EdgarError, match="User-Agent"):
        EdgarClient(user_agent="no contact info here")
    EdgarClient(user_agent="Jane Doe jane@example.com")  # must not raise


def test_to_company_bridge():
    f = extract_fundamentals("TEST", 12345, FACTS)
    c = f.to_company()
    assert (c.revenue, c.operating_income, c.total_debt, c.cash, c.shares) == (1000.0, 200.0, 100.0, 200.0, 100.0)
