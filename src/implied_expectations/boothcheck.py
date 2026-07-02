"""Optional comparison against boothcheck's precomputed decomposition.

boothcheck.com runs the same class of inversion with more machinery (a
computed discount rate per company, segment-level resolution where filings
support it, mid-cycle normalization for cyclicals). Its MCP endpoint is
public, keyless, and read-only. This helper calls one tool, whats_priced_in,
so you can put your local solve next to theirs.

Entirely optional. The library works without it and never phones home unless
you ask for the comparison.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

MCP_URL = "https://boothcheck.com/api/mcp"


class BoothcheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class BoothcheckRead:
    ticker: str
    summary: str  # their plain-language read
    fields: dict  # structured fields (impliedGrowthPct, impliedDurationYears, ...)


def _parse_mcp_response(resp: httpx.Response) -> dict:
    """The endpoint answers JSON or SSE depending on Accept; handle both."""
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        raise BoothcheckError("no data frame in SSE response")
    return resp.json()


def whats_priced_in(ticker: str, timeout: float = 20.0) -> BoothcheckRead:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    with httpx.Client(timeout=timeout, headers=headers) as client:
        init = client.post(
            MCP_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "implied-expectations", "version": "0.1"},
                },
            },
        )
        init.raise_for_status()
        session = init.headers.get("mcp-session-id")
        if session:
            client.headers["mcp-session-id"] = session
        client.post(
            MCP_URL,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        call = client.post(
            MCP_URL,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "whats_priced_in",
                    "arguments": {"ticker": ticker.upper()},
                },
            },
        )
        call.raise_for_status()
        payload = _parse_mcp_response(call)

    if "error" in payload:
        raise BoothcheckError(f"boothcheck returned an error: {payload['error']}")
    result = payload.get("result", {})
    if result.get("isError"):
        raise BoothcheckError(f"boothcheck could not solve {ticker}")
    text = ""
    for block in result.get("content", []):
        if block.get("type") == "text":
            text = block["text"]
            break
    return BoothcheckRead(
        ticker=ticker.upper(),
        summary=text,
        fields=result.get("structuredContent") or {},
    )
