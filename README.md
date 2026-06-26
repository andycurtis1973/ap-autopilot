# AP Autopilot

Agentic **invoice-to-pay** — intelligent document processing for accounts
payable, built on **Amazon Bedrock AgentCore**. An agent reads a vendor invoice
PDF, extracts the fields, runs the accounts-payable controls (math + a full
**three-way match** against the purchase order and goods receipt), then either
**auto-approves and posts to the ERP** or **routes the exception to a human with
the reason**. Duplicates are rejected — never paid twice.

> The model reads the document and decides *what* to check. The arithmetic is
> run by deterministic code (the AgentCore **Code Interpreter**), so a 3-way
> match is never at the mercy of LLM mental math.

## What it does

```
invoice.pdf ─▶ extract (Bedrock vision) ─▶ lookup PO + goods receipt (Gateway/MCP)
            ─▶ validate + 3-way match (Code Interpreter) ─▶ decide
                                                              ├─ clean      → post to ERP   (auto)
                                                              ├─ exception  → route to human (with reason)
                                                              └─ duplicate  → reject
```

On a generated batch of 19 invoices (13 clean + planted exceptions), the live
run clears **68% straight-through**, catches every planted exception with a
human-readable reason, and projects **~$8.7M/year saved** at 100k invoices/month
(industry-benchmark $11/invoice manual cost).

## Every AgentCore component, used for real

| Component | Role in AP Autopilot | Verified |
|---|---|---|
| **Runtime** | Hosts the Strands agent (ARM64 container, built by CodeBuild) | deployed + invoked live |
| **Gateway** | `lookup_purchase_order` / `lookup_goods_receipt` / `post_to_erp` / `route_for_approval` as MCP tools over a Lambda, behind Cognito OAuth | OAuth → MCP `tools/list` → tool call returns live PO data |
| **Code Interpreter** | Runs the invoice/3-way-match arithmetic in a sandbox | matches the pure-Python controls exactly |
| **Memory** | Per-vendor invoice history → duplicate-payment guard | create → record → detect live |
| **Observability** | OTEL traces + CloudWatch logs on Runtime and Gateway | on by default |

Extraction uses **Claude Sonnet 4.6** vision (`us.anthropic.claude-sonnet-4-6`):
**100% field accuracy** across all 18 sample PDFs for **$0.15** total.

## Quick start (no AWS needed)

The library, the document generator, and the controls are pure Python — the
offline parts never call AWS.

```bash
git clone https://github.com/andycurtis1973/ap-autopilot
cd ap-autopilot
python3 -m pip install pytest reportlab pymupdf

pytest -q                                          # full suite, AWS mocked — never spends
PYTHONPATH=src python3 scripts/gen_docs.py         # generate invoice PDFs + PO/receipt records -> data/
PYTHONPATH=src python3 scripts/run_offline.py      # run extract→validate→3-way-match→decide offline
```

`data/invoices/` then holds real invoice **PDFs** (open them — they look like
vendor invoices), and `run_offline.py` prints the decision and exception for each.

## Run it live on AWS

Needs AWS credentials in `us-east-1` with **Amazon Bedrock** (Claude Sonnet 4.6)
and **Bedrock AgentCore** access. Spend is a few dollars; everything tears down.

```bash
python3 -m pip install boto3 bedrock-agentcore bedrock-agentcore-starter-toolkit strands-agents

# 1) real extraction — read the PDFs with Claude vision, score vs ground truth
PYTHONPATH=src python3 scripts/run_extract.py --max-spend 0.40

# 2) the agent, end to end on live Bedrock (extract → 3-way-match workflow → post/route)
PYTHONPATH=src python3 scripts/run_agent.py data/invoices/INV-20274_price_variance.pdf

# 3) deploy the agent to AgentCore Runtime (CodeBuild ARM build), invoke it, tear down
PYTHONPATH=src deploy/demo.py up
PYTHONPATH=src deploy/demo.py invoke
PYTHONPATH=src deploy/demo.py down

# 4) the AgentCore Gateway: AP tools as MCP behind Cognito OAuth
PYTHONPATH=src deploy/gateway.py up
PYTHONPATH=src deploy/gateway.py test      # OAuth token → MCP tools/list → call a tool
PYTHONPATH=src deploy/gateway.py down
```

### Customer-facing visual

`deploy/capture.py` drives a real run (live extraction + the agentic workflow),
records each step, and produces a **self-contained animated `deploy/web/demo.html`**
— the actual invoice PDF, the extracted fields, the agent's tool trace, the
decision, and the straight-through-rate / cost / savings counters. No creds to
view it.

```bash
PYTHONPATH=src python3 deploy/capture.py        # -> deploy/web/demo.html
open deploy/web/demo.html
```

## Layout

```
src/ap_autopilot/
  model.py        Invoice / PurchaseOrder / GoodsReceipt / Decision (Decimal money, JSON round-trip)
  generate.py     reportlab invoice PDFs + PO/receipt records; planted exception mix
  pageimage.py    PDF → PNG (PyMuPDF) for vision
  extract.py      Bedrock vision extraction (Converse); mockable Extractor protocol
  validate.py     invoice math validation (line extensions, subtotal, total)
  match.py        three-way match vs PO + receipt, with a single MatchPolicy of tolerances
  pipeline.py     decide(): controls → auto-approve | route | reject (+ duplicate guard)
  tools.py        the AP tool backend (PO/receipt lookup, ERP post, approval routing)
  agent.py        the Strands agent + tools (lookup → validate_and_match → post/route)
  agentcore.py    live AgentCore: Code Interpreter math + Memory vendor history
  metrics.py      STP rate, cost, latency, annual savings projection
  bedrock_models.py  model ids + pricing
scripts/
  gen_docs.py     generate the document set
  run_offline.py  offline pipeline over the set (no AWS spend)
  run_extract.py  live Bedrock vision extraction + accuracy score
  run_agent.py    drive the agent end-to-end on real PDFs (live Bedrock)
deploy/
  demo.py         AgentCore Runtime: up / invoke / down (live, self-tears-down)
  runtime/        the deployed agent (AgentCore Runtime entrypoint + requirements)
  gateway.py      AgentCore Gateway: up / test / down (Lambda + Cognito + MCP)
  gateway_lambda.py  the AP tools as a Gateway Lambda target
  capture.py      drive a real run → animated demo.html
  web/template.html  the visual template
tests/            AWS-free suite (model, validate, match, pipeline, extract)
```

## Design decisions

- **Money is `Decimal`, never `float`.** Currency arithmetic on floats produces
  phantom one-cent math-validation failures. Quantities are `Decimal` too.
- **The model never does the math.** Extraction transcribes numbers *exactly as
  printed* (so a wrong total stays wrong and gets caught); validation and the
  3-way match are deterministic code (and the AgentCore Code Interpreter live).
- **Tolerances are one reviewed object** (`MatchPolicy`), not scattered magic
  numbers — "what counts as a price variance" is a single decision.
- **Severity drives the decision.** A BLOCKER (missing PO, duplicate, goods not
  received) rejects; a REVIEW item routes to a human; otherwise straight-through.
- **The controls are runtime-agnostic.** `decide()` is a pure function, so the
  unit suite, the local agent, and the deployed AgentCore Runtime run identical
  logic.

## Run the tests

```bash
pytest -q
```

## License

MIT — see [LICENSE](LICENSE).
