"""implied-expectations: invert a stock price into the bet it implies.

A textbook expectations-investing reverse DCF (Rappaport/Mauboussin framing):
given today's price and a company's SEC filings, solve for the revenue growth
rate, the years it must be sustained, and the operating margin that today's
price already assumes. Never a fair value, never a price target, never a
rating. It shows the bet, so you can judge it.
"""

from .edgar import EdgarClient, EdgarError, Fundamentals, extract_fundamentals
from .model import Assumptions, Company, Valuation, enterprise_value, value_per_share
from .solver import (
    GrowthSolution,
    SolveMode,
    implied_duration,
    implied_growth,
    implied_margin,
    sensitivity_grid,
)

__version__ = "0.1.0"

__all__ = [
    "Assumptions",
    "Company",
    "EdgarClient",
    "EdgarError",
    "Fundamentals",
    "GrowthSolution",
    "SolveMode",
    "Valuation",
    "enterprise_value",
    "extract_fundamentals",
    "implied_duration",
    "implied_growth",
    "implied_margin",
    "sensitivity_grid",
    "value_per_share",
]
