# Inventory Agent

An LLM-driven inventory assistant added to the catalyst-order-workflow demo.
The agent answers questions about stock, forecasts demand, and places durable
reposition orders. Reposition fulfillment lives in the inventory service as a
pair of Dapr workflows; the agent reaches them via Dapr service invocation
through a `trigger_reposition` tool.

## Requirements

### Functional
- **Interactive, not autonomous.** Users trigger an agent run via
  `POST /agent/run`; the agent is reactive, no continuous loop.
- **Capabilities exposed to the LLM** (five tools, see `tools.py`):
  - Report current inventory levels.
  - Summarize recent sales for a SKU over a rolling window.
  - Forecast demand over a horizon using a simple moving average.
  - List in-flight reposition orders.
  - Place a new reposition order (one combined order for any number of SKUs).
- **Decision rule** (LLM is prompted to follow it):
  Order if `forecast(horizon) ≥ current_stock` **and** no in-flight order
  exists for that SKU. Horizon defaults to 6 min (`HORIZON_MIN`) — chosen
  as the reposition lead time (5 min) plus a one-minute buffer.
- **In-flight awareness.** The agent must avoid double-ordering an SKU that
  already has a pending delivery.

### Non-functional
- **Durability of repositions.** Placing an order, the wait for delivery
  confirmation, and stock application must survive process restarts. The
  delivery workflow parks on an external `delivery_confirmed` event raised
  via `POST /api/v1/inventory/orders/<id>/confirm`, so the wait can outlive
  any process and the confirmer can amend quantities at arrival time.
- **Separation of concerns.** The inventory service owns inventory state,
  sales history, forecasting, and the reposition lifecycle. The agent host is
  a thin LLM-over-tools layer with no domain endpoints of its own. The two
  layers are independently testable.
- **LLM via Catalyst Conversation API.** The LLM endpoint, model, and key
  live in a Dapr Conversation component (`resources/llm-provider.yaml`).
  Swapping providers (OpenAI, Ollama, Anthropic, …) is a YAML edit; no
  application code changes. The component in this demo points at local
  Ollama (`qwen2.5:7b`) at `http://localhost:11434/v1`.

### Parameters (configurable via env / component metadata)
| Setting | Default | Where |
|---|---|---|
| Reposition lead time (ETA estimate only) | 5 min | `LEAD_TIME_MIN` env (inventory service) |
| SMA window | 5 min | `SMA_WINDOW_MIN` env (inventory service + agent) |
| Forecast horizon | 6 min | `HORIZON_MIN` env (inventory service + agent) |
| Inventory service app-id (for service invocation) | `inventory` | `INVENTORY_APP_ID` env (agent) |
| State store | `statestore` | `STATESTORE_NAME` env (both services) |
| LLM model / endpoint | qwen2.5:7b @ localhost:11434/v1 | `resources/llm-provider.yaml` |
| SKUs | `orange, apple, pear, kiwi` | inventory service |

## Implementation

### Service layout
```
services/
├── inventory/                      # owns inventory + sales + reposition workflows
│   ├── app.py                      # Flask endpoints + workflow runtime
│   ├── order_state.py              # reposition state helpers
│   ├── sales_state.py              # sales-history aggregation + forecast
│   └── workflows/
│       ├── __init__.py             # re-exports the workflows + activities
│       ├── restock_order.py        # registers the order, starts delivery
│       └── delivery.py             # waits for delivery_confirmed event, applies stock
└── inventory-agent/                # LLM agent — no custom endpoints
    ├── app.py                      # AgentRunner.serve(agent) wrapper
    ├── agent.py                    # DurableAgent + system prompt
    └── tools.py                    # five tools; Dapr service invocation into inventory
resources/
└── llm-provider.yaml               # Dapr Conversation component (LLM)
```

### Flow
```
User → POST /agent/run                              (inventory-agent — provided by AgentRunner)
        │
        ▼
   DurableAgent workflow ─── LLM ↔ tools loop ──────┐
   (LLM via DaprChatClient → llm-provider component)│
        │                                            │
        │       get_inventory_levels    ──svc-invoke──►  inventory service
        │       get_sales_history       ──svc-invoke──►  inventory service
        │       forecast_demand         ──svc-invoke──►  inventory service
        │       get_pending_repositions ──svc-invoke──►  inventory service
        │       trigger_reposition      ──svc-invoke──►  POST /api/v1/inventory/orders
        │                                                │
        ▼                                                ▼
   instance_id returned                       restock_order_workflow
   (poll GET /agent/instances/{id})            │
                                               ├─ register_order activity
                                               │     state: reposition:<id>
                                               │     state: repositions:active
                                               │
                                               └─ start child: delivery_workflow
                                                   │
                                                   ├─ wait_for_external_event("delivery_confirmed")
                                                   │     payload: {message?, amendments?}
                                                   │     (raised by POST /api/v1/inventory/orders/<id>/confirm)
                                                   └─ deliver_order activity
                                                         applies amendments or original items
                                                         marks record delivered
                                                         records confirmation_message / amendments
                                                         removes id from active list
```

### Key endpoints (inventory service)
| Verb | Path | Purpose |
|---|---|---|
| `GET`    | `/api/v1/inventory`                       | current levels |
| `POST`   | `/api/v1/inventory/reserve`               | reserves `qty` units of one SKU (default 1) — atomic decrement + sale-history append (etag-guarded, retries on conflict), publishes `sales` event |
| `POST`   | `/api/v1/inventory/restock`               | demo reset — destructively set all SKUs to 100 |
| `POST`   | `/api/v1/inventory/reposition`            | additive — `{items: {sku: qty}}` |
| `GET`    | `/api/v1/inventory/sales/history?sku=…`   | recent-window sales summary for an SKU |
| `GET`    | `/api/v1/inventory/forecast?sku=…&horizon_min=…&window_min=…` | SMA-based forecast; `window_min` defaults to `SMA_WINDOW_MIN` |
| `POST`   | `/api/v1/inventory/orders`                | start a `restock_order_workflow` |
| `GET`    | `/api/v1/inventory/orders/active`         | list in-flight orders |
| `GET`    | `/api/v1/inventory/orders/<id>`           | get a specific order |
| `POST`   | `/api/v1/inventory/orders/<id>/confirm`   | raise `delivery_confirmed` on the delivery workflow; body `{message?, amendments?: {sku: qty}}`. Amendments override the originally ordered quantities. |

### Key endpoints (agent service)
All provided by `AgentRunner.serve()`; there is no hand-written endpoint code
in the agent host.

| Verb | Path | Purpose |
|---|---|---|
| `POST` | `/agent/run`                                       | schedule a new agent run; returns `{instance_id, status_url}` |
| `GET`  | `/agent/instances/{instance_id}`                   | fetch status and final output |
| `POST` | `/agent/instances/{instance_id}/terminate`         | terminate a running instance |
| `POST` | `/agent/instances/{instance_id}/purge`             | purge instance state |

### LLM via Conversation API
`agent.py` constructs the LLM as
`DaprChatClient(component_name=LLM_COMPONENT)` (defaults to
`"llm-provider"`). The component (`resources/llm-provider.yaml`) is:

```yaml
apiVersion: dapr.io/v1alpha1
kind: Component
metadata:
  name: llm-provider
spec:
  type: conversation.openai
  version: v1
  metadata:
    - name: key
      value: ollama          # placeholder; Ollama ignores it
    - name: model
      value: qwen2.5:7b
    - name: endpoint
      value: http://localhost:11434/v1
scopes:
  - inventory-agent
```

To run against OpenAI, drop `endpoint`, change `key` to the real API key, and
pick a model. No agent code changes.

### Sales history
The inventory service records each `/reserve` directly into the state store
under `sales:<sku>` (capped at 60 entries), then publishes a `sales` event
on the `pubsub` component for any analytics subscribers. The agent does not
subscribe to anything — it pulls history and forecasts via the two `GET`
endpoints above.

### Reposition state
Two key kinds in the shared Dapr state store:
- `reposition:<order_id>` — full record. At placement time: `items`,
  `placed_at`, `eta`, `status: "placed"`, `delivered_at: null`. On delivery,
  `status: "delivered"`, `delivered_at`, `delivered_items` (the quantities
  actually applied — equal to `items` unless amended), and, when present,
  `confirmation_message` and `amendments`. Kept forever (demo).
- `repositions:active` — list of currently in-flight ids. Populated by
  `register_order`, drained by `deliver_order`. Single source of truth for
  the `get_pending_repositions` tool.

### Agent host (`services/inventory-agent/app.py`)
The entire host is:

```python
runner = AgentRunner()
try:
    runner.serve(build_agent(), port=APP_PORT)
finally:
    runner.shutdown()
```

`AgentRunner.serve` starts the agent, creates a FastAPI app, registers the
`/agent/run`, `/agent/instances/{id}`, `/agent/instances/{id}/terminate`
and `/agent/instances/{id}/purge` routes on it, and runs uvicorn. No
Flask, no manual `DaprWorkflowClient.schedule_new_workflow`, no
`_dapr_alternate_name` stub.

### Agent definition (`services/inventory-agent/agent.py`)
`DurableAgent` is configured with three Dapr-backed concerns (the store name
comes from `STATESTORE_NAME`, default `statestore`):

| Config | Purpose |
|---|---|
| `llm = DaprChatClient(component_name=LLM_COMPONENT)` | Conversation API |
| `state = AgentStateConfig(store=StateStoreService(store_name=STATESTORE_NAME))` | durable workflow state |
| `memory = AgentMemoryConfig(store=ConversationDaprStateMemory(store_name=STATESTORE_NAME))` | conversation history across runs |

`AgentRegistryConfig` and `AgentPubSubConfig` are intentionally omitted —
this agent is single, not part of an orchestrated team, and is only
triggered via HTTP.

## Running the demo

```bash
# One-time
curl https://ollama.com/install.sh | sh
ollama pull qwen2.5:7b
./build-apps.sh

# Each session
ollama serve &                                                # local LLM
diagrid dev run -f dapr.yaml --project $WORKFLOW_PROJECT_NAME # services
curl -X POST http://localhost:3002/api/v1/inventory/restock   # seed stock
./order-generator.sh -r 1 &                                   # drive sales

# Trigger the agent (fire-and-poll)
curl -X POST http://localhost:3005/agent/run \
  -H "Content-Type: application/json" \
  -d '{"task": "Forecast each SKU and order what we need."}'

# → {"instance_id": "...", "status_url": "/agent/instances/..."}

curl http://localhost:3005/agent/instances/<instance_id>
```

Sample REST Client calls are in `test-commands.http`.
