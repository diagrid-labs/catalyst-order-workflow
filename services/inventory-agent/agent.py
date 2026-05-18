"""Inventory assistant agent.

An LLM-driven assistant that helps a small store keep its shelves stocked.
It can:

  - Report current inventory levels for every SKU.
  - Summarize recent sales and forecast near-term demand.
  - List in-flight reposition orders.
  - Place new reposition orders when stock is at risk of running out, while
    avoiding double-ordering SKUs that already have a delivery on the way.

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
- Forecast demand over a horizon (default 6 minutes, which is the time it
  takes a reposition order to arrive plus one minute).
- Recommend or place reposition orders.

Decision rule for placing a reposition for a SKU:
  Order if the forecast over the horizon is greater than or equal to the
  current stock AND there is no in-flight reposition order for that SKU.

Always call get_pending_repositions before placing any new reposition, and
skip any SKU that already has an in-flight order. After placing an order,
report the order id and ETA.

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
