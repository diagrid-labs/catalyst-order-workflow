# Catalyst Order Processing Workflow

This solution demonstrates the capabilities provided by all five Catalyst APIs — and the Conversation API — through a python-based order processing workflow. The end-to-end solution is comprised of six services:

- **order-processor**: Contains the order process workflow definition and all associated activity methods which will be executed as part of the workflow sequence using the Catalyst Workflow API. Orders carry a `qty`; the workflow prices each order from the inventory catalog (unit price × qty) before reserving stock and taking payment. An explicit `total` can be set on the request to override auto-pricing — useful, for example, to force the >$1000 approval branch (`{"customer": "veronica", "item": "kiwi", "qty": 2, "total": 1299.00}`).
- **inventory**: Receives direct invocation requests sent by the order-processor using the Invocation API to manage inventory state in the Diagrid KV Store through the Catalyst State API. Each catalogued SKU has a name and a unit price (`INVENTORY_ITEMS` in `services/inventory/app.py`); `/restock` and `/reposition` only mutate quantity, never price. Also owns sales history, demand forecasting, and the durable reposition-order workflows that the agent invokes.
- **notifications**: Subscribes to messages published by the order-processor using the Pub/Sub API and subsequently displays those messages through a simple JavaScript user interface.
- **shipping**: Receives direct invocation requests sent by the order-processor using the Invocation API to simulate the scheduling of order shipments.
- **payments**: Receives direct invocation requests sent by the order-processor using the Invocation API to mock the processing of order payments.
- **inventory-agent**: An LLM-driven inventory assistant built with [dapr-agents](https://github.com/dapr/dapr-agents). Users send natural-language prompts; the agent inspects stock, forecasts demand, and places reposition orders by invoking the inventory service's tools. The LLM is reached through the Catalyst Conversation API component in `resources/llm-provider.yaml`. See [INVENTORY_AGENT.md](./INVENTORY_AGENT.md) for the design.

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
helm repo add otwld https://helm.otwld.com/ && helm repo update otwld
helm upgrade --install ollama otwld/ollama \
  --kube-context kind-onebox-cp \
  --namespace ollama --create-namespace \
  -f deploy/ollama/values.yaml
# First pod startup pulls qwen2.5:7b (~4.7 GB) into the PVC — wait for Ready.
kubectl --context kind-onebox-cp -n ollama rollout status deploy/ollama

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
