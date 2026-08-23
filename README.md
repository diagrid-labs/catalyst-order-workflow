# Catalyst Order Processing Workflow

This solution demonstrates the capabilities provided by all five Catalyst APIs — and the Conversation API — through a python-based order processing workflow. The end-to-end solution is comprised of six services:

- **order-processor**: Exposes the orders REST API in `app.py` and the order workflow with its activities in `order.py`, executed using the Catalyst Workflow API. Orders carry a `qty`; the workflow prices each order from the inventory catalog (unit price × qty) before reserving stock and taking payment. An explicit `total` can be set on the request to override auto-pricing — useful, for example, to force the >$1000 approval branch (`{"customer": "veronica", "item": "kiwi", "qty": 2, "total": 1299.00}`). The workflow collects a compensation for each step it takes and unwinds them in reverse if anything fails, so a failed order gives its reserved stock back and refunds any charge. Taking the money and shipping the goods are child workflows hosted by the payments and shipping services, called across the app boundary. A dispatch that fails restocks the goods and refunds the customer.
- **inventory**: Receives direct invocation requests sent by the order-processor using the Invocation API to manage inventory state in the Diagrid KV Store through the Catalyst State API. Each catalogued SKU has a name and a unit price (`INVENTORY_ITEMS` in `services/inventory/app.py`); `/restock` and `/reposition` only mutate quantity, never price. Reservations are provisional: `/reserve` moves stock but records no sale, `/confirm` turns a reservation into a sale once the order has shipped, `/release` gives back stock that was never sold, and `/return` puts a sold order's stock back on the shelf when its delivery fails. Each acts on one reservation state and ignores the others, so a caller can hold both reversal paths and exactly one of them credits the stock. The sale is published to the `sales` topic and appended to the history by a subscriber, so no request handler writes more than one key. Also owns sales history and demand forecasting.
- **notifications**: Subscribes to messages published by the order-processor, payments and shipping services using the Pub/Sub API and subsequently displays those messages through a simple JavaScript user interface. All three publishers go through `shared/notifications.py`, which owns the component and topic names; each workflow keeps its own `notify` activity and calls that to publish. `dapr.yaml` puts `shared/` on `PYTHONPATH` for every app.
- **shipping**: Owns fulfilment. `/shipping/ship` accepts an order, and `/shipping/prepare`, `/shipping/dispatch` and `/shipping/deliver` carry out the individual steps. `services/shipping/delivery.py` holds the delivery workflow that the order-processor starts as a child workflow in this app; it prepares the shipment and dispatches it, each step calling the matching endpoint through the Invocation API. `POST /shipping/deactivate` makes every endpoint return 503, which is how the demo exercises the order workflow's compensation path.
- **payments**: Owns taking the money. `/api/v1/payments` mocks a charge and `/api/v1/payments/<id>/refunds` mocks a refund. `services/payments/payment.py` holds the payment workflow the order-processor starts as a child workflow in this app; a declined card comes back as an unsuccessful result and fails the order cleanly, while anything else is a fault. The refund stays an activity on the order-processor, because it runs as a compensation.
- **inventory-agent**: An LLM-driven inventory assistant built with [dapr-agents](https://github.com/dapr/dapr-agents). Users send natural-language prompts; the agent inspects stock, forecasts demand, and adds stock by invoking the inventory service's tools. The LLM is reached through the Catalyst Conversation API component in `resources/llm-provider.yaml`. See [INVENTORY_AGENT.md](./INVENTORY_AGENT.md) for the design.

## Prerequisites
- [Sign up](https://catalyst.diagrid.io) for Diagrid Catalyst
- Install latest [Diagrid CLI](https://docs.diagrid.io/catalyst/references/cli-reference/intro#installing-the-cli)
- Install [Python3](https://www.python.org/downloads/)

### Prepare applications

The build apps file will go through each application directory and run any app commands necessary to prepare the applications to run. Make sure it is executable using the below command:

```bash
chmod +X build-apps.sh
```

Run the script:

```bash
./build-apps.sh
```

## Deploy Catalyst resources

Replace `unique-project-name` with a name for your workflow project

```bash
export WORKFLOW_PROJECT_NAME="unique-project-name"
```

Execute the following command to run your applications locally and create the dependent Catalyst resources:

```bash
diagrid dev run -f dapr.yaml --project $WORKFLOW_PROJECT_NAME
```

## Use the APIs

A `test.rest` file is available at the root of this repository and can be used with the VS Code `Rest Client` extension.
    ![Rest Client](/images/rest-client.png)

## Talking to the inventory-agent

The `inventory-agent` service exposes the standard dapr-agents HTTP surface on port `3005`. Trigger a run with a natural-language task and poll for the result:

```bash
# 1. Make sure an LLM is reachable. The bundled component points at an
#    in-cluster Ollama deployed via the otwld/ollama Helm chart. See
#    deploy/ollama/values.yaml for the values used.
#
#    The values request a GPU (nvidia.com/gpu). For this to schedule, the
#    cluster must be created with the minibox provider and the --enable-gpu
#    flag, i.e.:
#        one box dev up --provider minibox --enable-gpu
helm repo add otwld https://helm.otwld.com/ && helm repo update otwld
helm upgrade --install ollama otwld/ollama \
  --namespace ollama --create-namespace \
  -f deploy/ollama/values.yaml
# First pod startup pulls qwen2.5:7b (~4.7 GB) into the PVC — wait for Ready.
kubectl -n ollama rollout status deploy/ollama

# 2. Drive some sales so the forecasting tools have data to work with.
curl -X POST http://localhost:3002/api/v1/inventory/restock
./order-generator.sh -r 1 &

# 3. Ask the agent to do something.
curl -X POST http://localhost:3005/agent/run \
  -H "Content-Type: application/json" \
  -d '{"task": "Forecast each SKU and order what we need."}'
# → {"instance_id": "...", "status_url": "/agent/instances/..."}

# 4. Fetch the result.
curl http://localhost:3005/agent/instances/<instance_id>
```

To use a different LLM (OpenAI, Anthropic, Azure OpenAI, …), edit `resources/llm-provider.yaml` — no code changes are needed. See [INVENTORY_AGENT.md](./INVENTORY_AGENT.md) for the full agent design.
