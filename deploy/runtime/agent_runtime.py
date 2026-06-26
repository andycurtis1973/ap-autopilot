"""AP Autopilot agent — AgentCore Runtime entrypoint.

This is the deployed handler. The starter toolkit builds it into an ARM64
container (via CodeBuild) and hosts it on AgentCore Runtime with OTEL
Observability on. One invocation = one invoice processed end to end:

  payload {"pdf_b64": "..."}      → extract with Bedrock vision, then run the agent
  payload {"invoice": {...}}      → skip extraction, run the agent on given fields

Returns the decision, the agent's summary, and the ERP/approval ledger entry.
The procurement tools read the bundled records.json (the ERP stand-in); in a
real deployment they'd be AgentCore Gateway MCP tools over the live ERP.
"""

import base64
import json
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from ap_autopilot.model import Invoice, AUTO_APPROVED, ROUTED, REJECTED
from ap_autopilot.tools import APBackend
from ap_autopilot.pipeline import SeenStore
from ap_autopilot.agent import build_agent, invoice_prompt
from ap_autopilot.extract import BedrockVisionExtractor
from ap_autopilot.bedrock_models import DEFAULT

app = BedrockAgentCoreApp()

REGION = os.environ.get("AWS_REGION", "us-east-1")
RECORDS = os.path.join(os.path.dirname(__file__), "records.json")
_backend = APBackend(RECORDS)
_seen = SeenStore()
_extractor = BedrockVisionExtractor(model_id=DEFAULT.model_id, region=REGION)


def _extract_from_pdf(pdf_b64: str) -> Invoice:
    from ap_autopilot.pageimage import pdf_to_pngs
    import tempfile
    raw = base64.b64decode(pdf_b64)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(raw)
        path = f.name
    return _extractor.extract(pdf_to_pngs(path)).invoice


@app.entrypoint
def handler(payload):
    if payload.get("pdf_b64"):
        inv = _extract_from_pdf(payload["pdf_b64"])
    else:
        inv = Invoice.from_dict(payload["invoice"])

    agent = build_agent(_backend, _seen, model_id=DEFAULT.model_id, region=REGION)
    result = str(agent(invoice_prompt(inv))).strip()

    last = _backend.ledger[-1] if _backend.ledger else {}
    action = last.get("action")
    status = (AUTO_APPROVED if action == "post_to_erp"
              else ROUTED if action == "route_for_approval" else REJECTED)
    return {
        "invoice_id": inv.invoice_id,
        "vendor": inv.vendor_name,
        "total": str(inv.total),
        "status": status,
        "summary": result,
        "ledger_entry": last,
    }


if __name__ == "__main__":
    app.run()
