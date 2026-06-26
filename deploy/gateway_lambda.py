"""Lambda behind the AgentCore Gateway — exposes the AP procurement tools as MCP.

The Gateway turns this one Lambda into four MCP tools (lookup_purchase_order,
lookup_goods_receipt, post_to_erp, route_for_approval). AgentCore passes the
invoked tool name in the client context; we dispatch on it. This is the same
business logic as ap_autopilot.tools.APBackend, deployed as the live ERP/
procurement back end the hosted agent calls over MCP.

records.json (the ERP stand-in) is bundled alongside this handler.
"""

import json
import os

_RECORDS = None


def _records():
    global _RECORDS
    if _RECORDS is None:
        path = os.path.join(os.path.dirname(__file__), "records.json")
        with open(path) as f:
            _RECORDS = json.load(f)
    return _RECORDS


_LEDGER = []


def _tool_name(context) -> str:
    # AgentCore Gateway puts the tool name in the client context custom field.
    cc = getattr(context, "client_context", None)
    if cc and getattr(cc, "custom", None):
        name = cc.custom.get("bedrockAgentCoreToolName", "")
        return name.split("___")[-1] if name else ""
    return ""


def handler(event, context):
    tool = _tool_name(context) or event.get("tool", "")
    args = event if isinstance(event, dict) else {}
    recs = _records()

    if tool == "lookup_purchase_order":
        po = recs["purchase_orders"].get(args["po_number"])
        return {"found": True, **po} if po else {"found": False, "po_number": args["po_number"]}

    if tool == "lookup_goods_receipt":
        r = recs["goods_receipts"].get(args["po_number"])
        return {"found": True, **r} if r else {"found": False, "po_number": args["po_number"]}

    if tool == "post_to_erp":
        v = {"action": "post_to_erp", "voucher_id": f"VCH-{len(_LEDGER)+1000}",
             "invoice_id": args["invoice_id"], "amount": args.get("amount"),
             "status": "scheduled_for_payment"}
        _LEDGER.append(v)
        return v

    if tool == "route_for_approval":
        t = {"action": "route_for_approval", "ticket_id": f"TKT-{len(_LEDGER)+5000}",
             "invoice_id": args["invoice_id"], "reason": args.get("reason"),
             "status": "awaiting_human_review"}
        _LEDGER.append(t)
        return t

    return {"error": f"unknown tool {tool!r}"}
