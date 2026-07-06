"""SigNoz usage/cost query adapter.

Implements UsageQueryPort by querying SigNoz for OTLP-based usage/cost log
entries correlated by trace ID. This is architecturally separate from
SigNozTelemetryAdapter (which serves TelemetryPort) since it queries
usage/cost records rather than trace, log, or error data — see
UsageQueryPort's docstring in core/ports.py.

Usage/cost log entries are expected to carry a numeric `cost_usd` attribute,
emitted by the LLM diagnosis backend's OTLP instrumentation and correlated to
the diagnosis's originating trace via `trace_id`.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from rounds.core.ports import UsageQueryPort

logger = logging.getLogger(__name__)


class SigNozUsageQueryAdapter(UsageQueryPort):
    """SigNoz-backed usage/cost query adapter via REST API."""

    def __init__(
        self,
        api_url: str,
        api_key: str = "",
        lookback_hours: int = 24,
        client: httpx.AsyncClient | None = None,
    ):
        """Initialize SigNoz usage query adapter.

        Args:
            api_url: Base URL for SigNoz API (e.g., http://localhost:3301)
            api_key: Optional API key for SIGNOZ-API-KEY header authentication
            lookback_hours: How far back to search for usage/cost log entries
                correlated with a trace ID.
            client: Optional pre-built httpx.AsyncClient to reuse (e.g. the
                SigNoz telemetry adapter's client), avoiding a redundant
                connection pool. When provided, close() will not close it —
                the owner of the shared client is responsible for that.
        """
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.lookback_hours = lookback_hours
        self._closed = False
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=self.api_url,
            headers=self._get_headers(),
            timeout=30.0,
        )

    def _get_headers(self) -> dict[str, str]:
        """Build request headers.

        SigNoz uses SIGNOZ-API-KEY header, not Authorization: Bearer.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["SIGNOZ-API-KEY"] = self.api_key
        return headers

    async def __aenter__(self) -> "SigNozUsageQueryAdapter":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the httpx client. Safe to call multiple times.

        No-op when the client was injected (reused from another adapter) —
        the owner of the shared client is responsible for closing it.
        """
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self.client.aclose()

    async def query_diagnosis_cost(self, trace_id: str) -> float:
        """Query the actual diagnosis cost (in USD) for a given trace.

        Queries SigNoz logs correlated by trace_id, then sums the `cost_usd`
        attribute across any matching usage/cost log entries. Cost resolution
        must never block diagnosis, so invalid input, unreachable backends,
        and missing data all return 0.0 rather than raising.
        """
        if not self._is_valid_trace_id(trace_id):
            logger.warning(f"Skipping usage cost query for invalid trace ID: {trace_id!r}")
            return 0.0

        try:
            now = datetime.now(UTC)
            since = now - timedelta(hours=self.lookback_hours)

            payload = {
                "start": int(since.timestamp() * 1000),
                "end": int(now.timestamp() * 1000),
                "step": 60,
                "variables": {},
                "compositeQuery": {
                    "queryType": "builder",
                    "panelType": "list",
                    "builderQueries": {
                        "A": {
                            "dataSource": "logs",
                            "queryName": "A",
                            "expression": "A",
                            "aggregateOperator": "noop",
                            "filters": {
                                "op": "AND",
                                "items": [
                                    {
                                        "key": {
                                            "key": "trace_id",
                                            "dataType": "string",
                                            "type": "",
                                            "isColumn": True,
                                        },
                                        "op": "=",
                                        "value": trace_id,
                                    }
                                ],
                            },
                            "limit": 100,
                            "offset": 0,
                            "pageSize": 100,
                            "orderBy": [{"columnName": "timestamp", "order": "desc"}],
                        }
                    },
                },
            }

            response = await self.client.post("/api/v3/query_range", json=payload)
            response.raise_for_status()

            data = response.json()
            total_cost = 0.0
            found = False
            for result in data.get("data", {}).get("result", []):
                for item in result.get("list", []):
                    cost = self._parse_cost(item)
                    if cost is not None:
                        total_cost += cost
                        found = True

            return total_cost if found else 0.0

        except httpx.HTTPError as e:
            logger.warning(
                f"Failed to query usage cost for trace_id={trace_id!r}: {e}", exc_info=True
            )
            return 0.0
        except Exception as e:
            logger.warning(
                f"Unexpected error querying usage cost for trace_id={trace_id!r}: {e}",
                exc_info=True,
            )
            return 0.0

    @staticmethod
    def _parse_cost(item: dict[str, Any]) -> float | None:
        """Extract a `cost_usd` attribute value from a query_range log list item.

        Item format: {"timestamp": "ISO8601", "data": {log fields}}
        """
        try:
            log_data = item.get("data", {})
            for attr_key in ("attributes_number", "attributes_string"):
                attrs = log_data.get(attr_key)
                if isinstance(attrs, dict) and "cost_usd" in attrs:
                    return float(attrs["cost_usd"])
            return None
        except (TypeError, ValueError) as e:
            logger.warning(f"Failed to parse cost from usage log entry: {e}")
            return None

    @staticmethod
    def _is_valid_trace_id(trace_id: str) -> bool:
        """Validate that a trace ID is a safe hex string (32 hex chars)."""
        if not trace_id or len(trace_id) != 32:
            return False
        return all(c in "0123456789abcdefABCDEF" for c in trace_id)
