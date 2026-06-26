# AP Autopilot — Specification

**Status:** v0.1
**Target runtime:** Amazon Bedrock AgentCore (us-east-1)
**Extraction model:** Claude Sonnet 4.6 vision (`us.anthropic.claude-sonnet-4-6`)

---

## 1. Summary

An agent that processes vendor invoices end to end: read the PDF, extract the
fields, run the accounts-payable controls, and decide whether to pay. It is an
**intelligent document processing** (IDP) pipeline for **accounts payable**,
hosted on Amazon Bedrock AgentCore.

The premise of AP automation: most invoices are *clean* — they reference a valid
purchase order, for the right vendor, at the agreed price and quantity, for goods
that were received. Those can be paid without a human ("straight-through"). The
job of the agent is to clear the clean ones automatically and surface only the
exceptions, each with a precise reason.

## 2. The documents

- **Invoice** (PDF) — what the vendor sends; the only thing extracted by vision.
- **Purchase order (PO)** — the authorization: contracted SKUs, quantities, prices.
- **Goods receipt** — proof the goods/services arrived (quantities received).

The PO and goods receipt are the system of record (here: `data/records.json`, an
ERP stand-in; in production, AgentCore Gateway MCP tools over the real ERP).

## 3. The controls

### 3.1 Math validation (single invoice)
- Each line: `amount == quantity × unit_price` (±$0.02 rounding tolerance).
- `subtotal == Σ line amounts`.
- `total == subtotal + tax`.

Extraction transcribes numbers **exactly as printed** — it must not "fix" a bad
total, or the error can't be caught.

### 3.2 Three-way match (invoice ↔ PO ↔ receipt)
- Invoice references a PO that exists, for the same vendor.
- Per line, billed `unit_price` ≤ PO price + tolerance.
- Per line, billed `quantity` ≤ ordered and ≤ received.
- Invoice subtotal ≤ PO total + tolerance (pre-tax; guards accumulated drift).

### 3.3 Duplicate guard
- `(vendor_id, invoice_id)` already processed ⇒ reject. Backed by AgentCore
  Memory (per-vendor history) in the live system.

## 4. Tolerances (`MatchPolicy`)

| Knob | Default | Meaning |
|---|---|---|
| `price_tolerance_pct` | 2% | per-line unit-price band vs PO… |
| `price_tolerance_abs` | $1.00 | …whichever is larger (small-dollar lines) |
| `qty_tolerance_units` | 0 | over-billing vs ordered/received never allowed |
| `total_tolerance_pct` | 1% | invoice-subtotal vs PO-total band… |
| `total_tolerance_abs` | $25.00 | …whichever is larger |

One object, one reviewed decision. Live, defaults can be overridden per vendor
from Memory.

## 5. Exception taxonomy → decision

Severity decides the outcome: any **BLOCKER** ⇒ `rejected`; else any **REVIEW**
⇒ `routed_for_approval`; else `auto_approved`.

| Code | Severity |
|---|---|
| `DUPLICATE_INVOICE`, `PO_NOT_FOUND`, `VENDOR_MISMATCH`, `QUANTITY_NOT_RECEIVED` (no receipt) | BLOCKER |
| `LINE_EXTENSION_ERROR`, `SUBTOTAL_ERROR`, `TOTAL_ERROR` | REVIEW |
| `PRICE_VARIANCE`, `OVER_PO_QUANTITY`, `QUANTITY_NOT_RECEIVED` (short), `UNORDERED_ITEM`, `SUBTOTAL_OVER_PO`, `NO_PO_REFERENCE` | REVIEW |

`auto_approved` → `post_to_erp`; otherwise → `route_for_approval` with the codes
and a human-readable reason.

## 6. AgentCore architecture

| Component | Use |
|---|---|
| **Runtime** | hosts the Strands agent (`deploy/runtime/agent_runtime.py`), ARM64 via CodeBuild |
| **Gateway** | the four AP tools as MCP over a Lambda, Cognito OAuth inbound |
| **Code Interpreter** | runs §3.1 math in a sandbox (`agentcore.CodeInterpreterMath`) |
| **Memory** | per-vendor invoice history → §3.3 duplicate guard |
| **Observability** | OTEL traces + CloudWatch logs (Runtime + Gateway) |

The agent's job is orchestration: it calls `lookup_purchase_order`,
`lookup_goods_receipt`, then `validate_and_match` (deterministic controls), and
finally `post_to_erp` or `route_for_approval`. It never does arithmetic itself.

## 7. Milestones (all implemented)

1. Document model + reportlab invoice/PO/receipt generation with planted exceptions.
2. Deterministic controls: validation + 3-way match + decision + duplicate guard (AWS-free tests).
3. Live Bedrock vision extraction (100% field accuracy on the sample set).
4. Strands agent over real Bedrock (clean → ERP, exception → review).
5. AgentCore Code Interpreter (math) + Memory (history) — verified live.
6. Deploy to AgentCore Runtime (Observability on) — verified `up/invoke/down`.
7. AgentCore Gateway — AP tools as MCP via Cognito OAuth — verified `up/test/down`.
8. Captured animated `demo.html` + narrated explainer video.
