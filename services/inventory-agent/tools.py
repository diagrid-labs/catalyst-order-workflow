"""Tools exposed to the inventory-agent.

Every tool is a thin HTTP call to the inventory service via Dapr service
invocation. The inventory service owns inventory state, sales history,
forecasting, and reposition orders — the agent only orchestrates over them.
"""

import json
import logging
import os

from dapr.aio.clients import DaprClient
from dapr_agents import tool
from pydantic import BaseModel, Field

INVENTORY_APP_ID = os.getenv("INVENTORY_APP_ID", "inventory")
SMA_WINDOW_MIN = int(os.getenv("SMA_WINDOW_MIN", 5))
HORIZON_MIN = int(os.getenv("HORIZON_MIN", 6))

logger = logging.getLogger("inventory_agent.tools")


async def _invoke(method: str, *, verb: str = "GET", data: dict | None = None) -> object:
    async with DaprClient() as d:
        kwargs = {"http_verb": verb}
        if data is not None:
            kwargs["data"] = json.dumps(data)
        resp = await d.invoke_method(INVENTORY_APP_ID, method, **kwargs)
    return json.loads(resp.data.decode("utf-8"))


class _NoArgs(BaseModel):
    pass


@tool(args_model=_NoArgs)
async def get_inventory_levels() -> dict:
    """Return the current inventory levels for all SKUs."""
    return await _invoke("api/v1/inventory")


class _SalesHistoryArgs(BaseModel):
    sku: str = Field(..., description="The SKU to fetch sales history for")
    window_min: int = Field(
        SMA_WINDOW_MIN,
        description="How many minutes of recent history to return",
    )


@tool(args_model=_SalesHistoryArgs)
async def get_sales_history(sku: str, window_min: int = SMA_WINDOW_MIN) -> dict:
    """Return recent sales for an SKU summarized over the given window."""
    return await _invoke(
        f"api/v1/inventory/sales/history?sku={sku}&window_min={window_min}"
    )


class _ForecastArgs(BaseModel):
    sku: str = Field(..., description="The SKU to forecast")
    horizon_min: int = Field(
        HORIZON_MIN,
        description="Forecast horizon in minutes (default covers one reposition cycle)",
    )
    window_min: int = Field(
        SMA_WINDOW_MIN,
        description="Minutes of recent sales the SMA averages over (default 5)",
    )


@tool(args_model=_ForecastArgs)
async def forecast_demand(
    sku: str,
    horizon_min: int = HORIZON_MIN,
    window_min: int = SMA_WINDOW_MIN,
) -> dict:
    """Forecast units sold for an SKU over the next `horizon_min` minutes,
    using the last `window_min` minutes of sales as the SMA window.
    """
    return await _invoke(
        f"api/v1/inventory/forecast?sku={sku}"
        f"&horizon_min={horizon_min}&window_min={window_min}"
    )


@tool(args_model=_NoArgs)
async def get_pending_repositions() -> list:
    """Return all reposition orders currently in flight.

    The agent must call this before placing a new reposition to avoid
    double-ordering.
    """
    return await _invoke("api/v1/inventory/orders/active")


class _TriggerRepositionArgs(BaseModel):
    items: dict[str, int] = Field(
        ...,
        description="Mapping of SKU to quantity. Example: {'orange': 5, 'apple': 12}",
    )


@tool(args_model=_TriggerRepositionArgs)
async def trigger_reposition(items: dict[str, int]) -> dict:
    """Place a reposition order for the given items.

    Starts a durable workflow that registers the order and adds it to the
    in-flight list immediately. The stock is applied later, when the
    delivery is confirmed via `POST /api/v1/inventory/orders/<id>/confirm`
    — not on a timer. Returns the created order record (`id`, `items`,
    `placed_at`, `status: "placed"`).
    """
    return await _invoke("api/v1/inventory/orders", verb="POST", data={"items": items})


def build_tools() -> list:
    return [
        get_inventory_levels,
        get_sales_history,
        forecast_demand,
        get_pending_repositions,
        trigger_reposition,
    ]
