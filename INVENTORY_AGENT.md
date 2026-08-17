# Inventory Agent

An LLM-driven inventory assistant added to the catalyst-order-workflow demo.
The agent answers questions about stock, forecasts demand, and adds stock for
SKUs at risk of running out. Every capability is an endpoint on the inventory
service, reached via Dapr service invocation.

## Requirements

### Functional
- **Interactive, not autonomous.** Users trigger an agent run via
  `POST /agent/run`; the agent is reactive, no continuous loop.
- **Capabilities exposed to the LLM** (four tools, see `tools.py`):
  - Report current inventory levels.
  - Summarize recent sales for a SKU over a rolling window.
  - Forecast demand over a horizon using a simple moving average.
  - Add stock (one combined call for any number of SKUs).
- **Decision rule** (LLM is prompted to follow it):
  Restock if `forecast(horizon) ≥ current_stock`. Horizon defaults to 6 min
  (`HORIZON_MIN`).
- **Immediate application.** Repositioned stock lands before the call returns,
  so the agent re-reads levels rather than tracking orders in flight.

### Non-functional
- **Separation of concerns.** The inventory service owns inventory state,
  sales history and forecasting. The agent host is a thin LLM-over-tools layer
  with no domain endpoints of its own. The two layers are independently
  testable.
- **LLM via Catalyst Conversation API.** The LLM endpoint, model, and key
  live in a Dapr Conversation component (`resources/llm-provider.yaml`).
  Swapping providers (OpenAI, Ollama, Anthropic, …) is a YAML edit; no
  application code changes. The component in this demo points at an
  in-cluster Ollama (`qwen2.5:7b`) deployed via the `otwld/ollama` Helm
  chart and exposed as `ollama.ollama.svc.cluster.local:11434`. The
  values used live in `deploy/ollama/values.yaml`.

### Parameters (configurable via env / component metadata)
| Setting | Default | Where |
|---|---|---|
| SMA window | 5 min | `SMA_WINDOW_MIN` env (inventory service + agent) |
| Forecast horizon | 6 min | `HORIZON_MIN` env (inventory service + agent) |
| Inventory service app-id (for service invocation) | `inventory` | `INVENTORY_APP_ID` env (agent) |
| State store | `statestore` | `STATESTORE_NAME` env (both services) |
| LLM model / endpoint | qwen2.5:7b @ ollama.ollama.svc.cluster.local:11434/v1 | `resources/llm-provider.yaml` |
| SKUs (name → unit price) | `orange:1.50, apple:2.00, pear:1.75, kiwi:3.00` | `INVENTORY_ITEMS` in inventory service |

## Implementation

### Service layout
```
services/
├── inventory/                      # owns inventory + sales history
│   ├── app.py                      # Flask endpoints
│   ├── state.py                    # state-store name + timestamp helper
│   └── sales_state.py              # sales-history aggregation + forecast
└── inventory-agent/                # LLM agent — no custom endpoints
    ├── app.py                      # AgentRunner.serve(agent) wrapper
    ├── agent.py                    # DurableAgent + system prompt
    └── tools.py                    # four tools; Dapr service invocation into inventory
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
        │       get_inventory_levels ──svc-invoke──►  GET  /api/v1/inventory
        │       get_sales_history    ──svc-invoke──►  GET  /api/v1/inventory/sales/history
        │       forecast_demand      ──svc-invoke──►  GET  /api/v1/inventory/forecast
        │       trigger_reposition   ──svc-invoke──►  POST /api/v1/inventory/reposition
        │                                                  adds to each SKU's quantity
        ▼                                                  and returns the new levels
   instance_id returned
   (poll GET /agent/instances/{id})
```

### Key endpoints (inventory service)
| Verb | Path | Purpose |
|---|---|---|
| `GET`    | `/api/v1/inventory`                       | current levels — returns `[{name, quantity, price}, …]`; the unit price is catalog data from `INVENTORY_ITEMS` and is never mutated by state operations |
| `POST`   | `/api/v1/inventory/reserve`               | provisionally take `qty` units of one SKU out of stock (default 1) — etag-guarded decrement plus a `pending` reservation record; no sale is recorded |
| `POST`   | `/api/v1/inventory/confirm`               | turn an order's reservation into a sale; publishes to the `sales` topic. Body `{id}` |
| `POST`   | `/api/v1/inventory/release`               | give a pending reservation's stock back. Body `{id}` |
| `POST`   | `/sales`                                  | subscriber for the `sales` topic — appends the sale to `sales:<sku>` |
| `POST`   | `/api/v1/inventory/restock`               | demo reset — destructively set every SKU's quantity to 100 (prices are not touched) |
| `POST`   | `/api/v1/inventory/reposition`            | additive `{items: {sku: qty}}` — adds to quantity only (prices are not touched) |
| `GET`    | `/api/v1/inventory/sales/history?sku=…`   | recent-window sales summary for an SKU |
| `GET`    | `/api/v1/inventory/forecast?sku=…&horizon_min=…&window_min=…` | SMA-based forecast; `window_min` defaults to `SMA_WINDOW_MIN` |

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
      value: http://ollama.ollama.svc.cluster.local:11434/v1
scopes:
  - inventory-agent
```

To run against OpenAI, drop `endpoint`, change `key` to the real API key, and
pick a model. No agent code changes.

### Sales history
A sale is recorded when an order completes, not when its stock is reserved, so
a failed order leaves no trace in the history. `/confirm` publishes to the
`sales` topic on the `pubsub` component; the inventory service subscribes to
that topic itself and appends the entry to `sales:<sku>` (capped at 60
entries). The agent does not subscribe to anything — it pulls history and
forecasts via the two `GET` endpoints above.

### Reservation state
One key kind beyond the per-SKU quantity: `reservation:<order_id>`, holding
`{order_id, status, sku, qty, remaining, reserved_at}` where `status` is
`pending`, `sold` or `released`. Pending records outlive the longest possible
order workflow (48h, since the approval gate alone allows 24h); terminal ones
expire after an hour, as their only remaining job is to absorb a retried call.

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
# One-time — deploy Ollama into the onebox cluster via the otwld chart.
# qwen2.5:7b (~4.7 GB) is pulled into the PVC on first pod start; subsequent
# restarts reuse the cached blob. See deploy/ollama/values.yaml.
helm repo add otwld https://helm.otwld.com/ && helm repo update otwld
helm upgrade --install ollama otwld/ollama \
  --kube-context kind-onebox-cp \
  --namespace ollama --create-namespace \
  -f deploy/ollama/values.yaml
kubectl --context kind-onebox-cp -n ollama rollout status deploy/ollama
./build-apps.sh

# Each session
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
