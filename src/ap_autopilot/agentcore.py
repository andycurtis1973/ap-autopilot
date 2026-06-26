"""Live AgentCore service integrations (optional — only imported on the AWS path):

  • CodeInterpreterMath  — runs the invoice arithmetic in the managed AgentCore
    **Code Interpreter** sandbox, so the numbers are checked by executed Python,
    never by the model. Mirrors validate.validate_math exactly.
  • AgentCoreMemory      — per-vendor invoice history in AgentCore **Memory**,
    backing the duplicate-payment guard (implements tools.VendorMemory).

Both degrade gracefully: if AgentCore isn't reachable the caller falls back to
the pure-Python equivalents, so nothing here is on the offline/test path.
"""

from __future__ import annotations

import json

from .model import Invoice, Exception_, SEVERITY_REVIEW, money

REGION = "us-east-1"


# --- Code Interpreter ------------------------------------------------------
_MATH_SNIPPET = r"""
import json
from decimal import Decimal, ROUND_HALF_UP
def m(x): return Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)
inv = json.loads(INVOICE_JSON)
TOL = Decimal("0.02")
exc = []
sub = sum((m(li["amount"]) for li in inv["line_items"]), Decimal("0"))
for li in inv["line_items"]:
    ext = m(Decimal(str(li["quantity"])) * m(li["unit_price"]))
    if abs(m(li["amount"]) - ext) > TOL:
        exc.append({"code":"LINE_EXTENSION_ERROR","sku":li["sku"],
                    "printed":str(m(li["amount"])),"computed":str(ext)})
if abs(m(inv["subtotal"]) - m(sub)) > TOL:
    exc.append({"code":"SUBTOTAL_ERROR","printed":str(m(inv["subtotal"])),"computed":str(m(sub))})
tot = m(m(inv["subtotal"]) + m(inv["tax"]))
if abs(m(inv["total"]) - tot) > TOL:
    exc.append({"code":"TOTAL_ERROR","printed":str(m(inv["total"])),"computed":str(tot)})
print(json.dumps(exc))
"""


class CodeInterpreterMath:
    """Validate invoice math by executing Python in the AgentCore sandbox."""

    def __init__(self, region: str = REGION):
        self.region = region

    def validate_math(self, inv: Invoice) -> list[Exception_]:
        from bedrock_agentcore.tools.code_interpreter_client import code_session

        payload = json.dumps(inv.to_dict())
        code = f"INVOICE_JSON = {json.dumps(payload)}\n" + _MATH_SNIPPET
        with code_session(self.region) as ci:
            resp = ci.invoke("executeCode", {"language": "python", "code": code})
            out = ""
            for ev in resp["stream"]:
                sc = ev.get("result", {}).get("structuredContent", {})
                if sc.get("stderr"):
                    raise RuntimeError(f"code interpreter error: {sc['stderr']}")
                out += sc.get("stdout", "")
        raw = json.loads(out.strip() or "[]")
        sev_map = {"LINE_EXTENSION_ERROR": SEVERITY_REVIEW, "SUBTOTAL_ERROR": SEVERITY_REVIEW,
                   "TOTAL_ERROR": SEVERITY_REVIEW}
        return [Exception_(r["code"], sev_map.get(r["code"], SEVERITY_REVIEW),
                           f"{r['code']}: printed {r.get('printed')} vs computed {r.get('computed')}",
                           {k: v for k, v in r.items() if k != "code"}) for r in raw]


# --- Memory ----------------------------------------------------------------
MEMORY_NAME = "ap_autopilot_vendor_history"


class AgentCoreMemory:
    """Per-vendor invoice history in AgentCore Memory (duplicate-payment guard).

    Each processed invoice is an event under actor_id=vendor, session="ledger".
    is_duplicate checks the vendor's recent history for the invoice id.
    """

    def __init__(self, memory_id: str | None = None, region: str = REGION,
                 session_id: str = "ledger"):
        from bedrock_agentcore.memory import MemoryClient

        self.client = MemoryClient(region_name=region)
        self.session_id = session_id
        if memory_id:
            self.memory_id = memory_id
        else:
            mem = self.client.create_or_get_memory(
                name=MEMORY_NAME, description="AP Autopilot per-vendor invoice history")
            self.memory_id = mem.get("memoryId") or mem.get("id")

    def is_duplicate(self, vendor_id: str, invoice_id: str) -> bool:
        try:
            events = self.client.list_events(
                memory_id=self.memory_id, actor_id=vendor_id,
                session_id=self.session_id, max_results=100)
        except Exception:
            return False
        for ev in events:
            for msg in ev.get("payload", []):
                blob = json.dumps(msg)
                if f'"invoice_id": "{invoice_id}"' in blob or invoice_id in blob:
                    return True
        return False

    def record_invoice(self, vendor_id: str, invoice_id: str) -> None:
        self.client.create_event(
            memory_id=self.memory_id, actor_id=vendor_id, session_id=self.session_id,
            messages=[(json.dumps({"invoice_id": invoice_id, "vendor_id": vendor_id}),
                       "ASSISTANT")])
