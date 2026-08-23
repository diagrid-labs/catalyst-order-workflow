"""Inventory-agent host.

Thin wrapper around `dapr_agents.workflow.runners.AgentRunner.serve`, which
exposes the standard durable-agent HTTP surface:

  POST /agent/run                        — schedule an agent run
  GET  /agent/instances/{instance_id}    — fetch status and result
  POST /agent/instances/{id}/terminate   — terminate a running instance
  POST /agent/instances/{id}/purge       — purge instance state

See ``agent.py`` for the agent definition and
``tools.py`` for the capabilities the LLM can call.
"""

import logging
import os

from dapr_agents.workflow.runners import AgentRunner

from inventory_agent.agent import build_agent

APP_PORT = int(os.getenv("APP_PORT", 3005))


def main():
    runner = AgentRunner()
    try:
        runner.serve(build_agent(), port=APP_PORT)
    finally:
        runner.shutdown()


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )
    main()
