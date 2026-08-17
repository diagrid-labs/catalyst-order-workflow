"""Inventory assistant agent.

An LLM-driven assistant that helps a small store keep its shelves stocked.
It can:

  - Report current inventory levels for every SKU.
  - Summarize recent sales and forecast near-term demand.
  - Add stock for SKUs at risk of running out.

Users interact with the agent through natural-language prompts ("how much
orange do we have?", "order what we need for the next hour"). The agent
decides which capabilities to invoke and in what order.
"""

import os

from dapr_agents import DurableAgent
from dapr_agents.agents.configs import AgentMemoryConfig, AgentStateConfig
from dapr_agents.llm import DaprChatClient
from dapr_agents.memory import ConversationDaprStateMemory
from dapr_agents.storage.daprstores.stateservice import StateStoreService

from tools import build_tools

LLM_COMPONENT = os.getenv("LLM_COMPONENT", "llm-provider")
STATESTORE_NAME = os.getenv("STATESTORE_NAME", "statestore")

AGENT_NAME = "InventoryAgent"

SYSTEM_INSTRUCTIONS = """\
You are an inventory assistant for a small store. The available SKUs are
orange, apple, pear, kiwi.

Capabilities:
- Report current inventory.
- Summarize recent sales for a SKU over a rolling window (default 5 minutes).
- Forecast demand over a horizon (default 6 minutes).
- Add stock for any number of SKUs in one call.

Decision rule for restocking a SKU:
  Restock if the forecast over the horizon is greater than or equal to the
  current stock. Order enough to cover the forecast with some margin.

Repositioned stock is applied immediately, so check the current levels before
deciding and report the new quantity per SKU afterwards. Put every SKU that
needs stock into a single trigger_reposition call.

Be concise. Show numbers, not paragraphs.
"""


def build_agent() -> DurableAgent:
    return DurableAgent(
        name=AGENT_NAME,
        role="Inventory assistant",
        instructions=[SYSTEM_INSTRUCTIONS],
        tools=build_tools(),
        llm=DaprChatClient(component_name=LLM_COMPONENT),
        state=AgentStateConfig(
            store=StateStoreService(store_name=STATESTORE_NAME),
        ),
        memory=AgentMemoryConfig(
            store=ConversationDaprStateMemory(store_name=STATESTORE_NAME),
        ),
    )
