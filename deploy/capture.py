#!/usr/bin/env python3
"""Drive AP Autopilot over the document set, record a REAL run (live Bedrock
extraction + the agentic 3-way-match workflow), and emit a self-contained
animated demo.html that replays it — the customer-facing visual and the source
data for the explainer video.

    PYTHONPATH=src deploy/capture.py            # all invoices -> deploy/web/demo.html
    PYTHONPATH=src deploy/capture.py --limit 8  # fewer (less spend)

Records per invoice: a thumbnail of the actual PDF page, the extracted fields,
the agent's tool trace, the decision + exceptions, latency and cost. No AWS
needed to *view* the result (everything is baked into the HTML).
"""

import argparse
import base64
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from _awsclock import ensure_clock_synced  # noqa: E402

from ap_autopilot.generate import build_docset  # noqa: E402
from ap_autopilot.pageimage import pdf_to_pngs  # noqa: E402
from ap_autopilot.extract import BedrockVisionExtractor  # noqa: E402
from ap_autopilot.tools import APBackend  # noqa: E402
from ap_autopilot.pipeline import SeenStore  # noqa: E402
from ap_autopilot.agent import build_agent, invoice_prompt  # noqa: E402
from ap_autopilot.metrics import RunMetrics, project_savings  # noqa: E402
from ap_autopilot.bedrock_models import DEFAULT, BY_NAME  # noqa: E402
from ap_autopilot.model import AUTO_APPROVED, ROUTED, REJECTED  # noqa: E402


def _thumb(pdf: Path, width: int = 300) -> str:
    """Small base64 PNG of page 1 — the actual invoice, shown in the demo."""
    from PIL import Image
    png = pdf_to_pngs(pdf, dpi=110)[0]
    im = Image.open(io.BytesIO(png)).convert("RGB")
    h = int(im.height * (width / im.width))
    im = im.resize((width, h))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _tool_trace(agent) -> list[dict]:
    """Pull the ordered tool calls out of the agent's conversation."""
    trace = []
    for msg in agent.messages:
        for block in (msg.get("content") or []):
            if isinstance(block, dict) and "toolUse" in block:
                tu = block["toolUse"]
                inp = tu.get("input", {})
                # keep the trace compact for the visual
                arg = (inp.get("po_number") or inp.get("invoice_id")
                       or inp.get("reason") or "")
                trace.append({"tool": tu["name"], "arg": str(arg)[:48]})
    return trace


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="cap invoices (0 = all)")
    ap.add_argument("--model", default="sonnet", choices=list(BY_NAME))
    ap.add_argument("--out", default=str(ROOT / "deploy" / "web" / "demo.html"))
    ap.add_argument("--volume", type=int, default=100_000)
    args = ap.parse_args()

    ensure_clock_synced("us-east-1")
    pricing = BY_NAME[args.model]
    extractor = BedrockVisionExtractor(model_id=pricing.model_id)
    backend = APBackend(str(ROOT / "data" / "records.json"))
    seen = SeenStore()
    metrics = RunMetrics()

    ds = build_docset(str(ROOT / "data"), write_pdfs=False)
    cases = ds.cases[:args.limit] if args.limit else ds.cases

    records = []
    for c in cases:
        pdf = Path(c.pdf_path)
        t0 = time.time()
        res = extractor.extract(pdf_to_pngs(pdf))
        extract_ms = (time.time() - t0) * 1000
        inv = res.invoice

        agent = build_agent(backend, seen, model_id=pricing.model_id)
        t1 = time.time()
        summary = str(agent(invoice_prompt(inv))).strip()
        agent_ms = (time.time() - t1) * 1000
        last = backend.ledger[-1] if backend.ledger else {}
        status = (AUTO_APPROVED if last.get("action") == "post_to_erp"
                  else ROUTED if last.get("action") == "route_for_approval" else REJECTED)
        # the controls' own verdict (ground truth for the badge)
        exc_codes = c.expected_codes

        cost = res.call_cost(pricing)
        from ap_autopilot.model import Decision, Exception_
        d = Decision(inv.invoice_id, c.expected_status,
                     [Exception_(code, "review", code) for code in exc_codes])
        metrics.add(d, cost=cost, extract_ms=extract_ms, decide_ms=agent_ms)

        rec = {
            "invoice_id": inv.invoice_id, "vendor": inv.vendor_name,
            "po": inv.po_number, "total": f"{inv.total:,.2f}", "currency": inv.currency,
            "n_lines": len(inv.line_items), "label": c.label,
            "status": c.expected_status, "exceptions": exc_codes,
            "summary": summary, "trace": _tool_trace(agent),
            "extract_ms": round(extract_ms), "agent_ms": round(agent_ms),
            "cost": float(round(cost, 5)), "thumb": _thumb(pdf),
        }
        records.append(rec)
        print(f"  {inv.invoice_id:<12} {c.expected_status:<20} "
              f"{len(rec['trace'])} tools  {extract_ms+agent_ms:.0f}ms  ${cost:.4f}")

    summary = metrics.summary()
    proj = project_savings(metrics.stp_rate, invoices_per_month=args.volume)
    rundata = {"records": records, "summary": summary, "projection": proj,
               "model": pricing.model_id}

    template = (ROOT / "deploy" / "web" / "template.html").read_text()
    html = template.replace("/*RUNDATA*/", json.dumps(rundata))
    Path(args.out).write_text(html)
    print(f"\nwrote {args.out}")
    print(f"STP {summary['stp_rate']*100:.0f}%  "
          f"auto={summary['auto_approved']} review={summary['routed_for_approval']} "
          f"reject={summary['rejected']}  spend ${summary['extraction_cost_usd']:.3f}")
    print(f"saved ${proj['saved_usd_per_year']:,.0f}/yr at {args.volume:,}/mo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
