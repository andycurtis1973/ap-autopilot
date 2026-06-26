#!/usr/bin/env python3
"""Drive the AP Autopilot agent end-to-end on real invoice PDFs with live
Bedrock: extract (vision) → agentic 3-way-match workflow → post/route. Prints
the tool trace so you can watch the agent reason.

    PYTHONPATH=src python3 scripts/run_agent.py data/invoices/INV-20273_math_error.pdf
    PYTHONPATH=src python3 scripts/run_agent.py --all --max-spend 0.50

Spends real money (extraction + agent reasoning, a few cents per invoice).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _awsclock import ensure_clock_synced  # noqa: E402

from ap_autopilot.pageimage import pdf_to_pngs  # noqa: E402
from ap_autopilot.extract import BedrockVisionExtractor  # noqa: E402
from ap_autopilot.tools import APBackend  # noqa: E402
from ap_autopilot.pipeline import SeenStore  # noqa: E402
from ap_autopilot.agent import build_agent, invoice_prompt  # noqa: E402
from ap_autopilot.bedrock_models import DEFAULT  # noqa: E402


def run_one(pdf: Path, extractor, backend, seen, verbose=True):
    res = extractor.extract(pdf_to_pngs(pdf))
    inv = res.invoice
    agent = build_agent(backend, seen)
    if verbose:
        print(f"\n=== {pdf.name} → extracted {inv.invoice_id} "
              f"(vendor {inv.vendor_name}, total {inv.total}) ===")
    result = agent(invoice_prompt(inv))
    # The ledger records the agent's terminal action (post or route).
    last = backend.ledger[-1] if backend.ledger else {}
    if verbose:
        print(f"agent: {str(result).strip()}")
        print(f"action: {last.get('action')} → {last.get('voucher_id') or last.get('ticket_id')}")
    return inv, last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max-spend", type=float, default=0.50)
    args = ap.parse_args()

    ensure_clock_synced("us-east-1")
    extractor = BedrockVisionExtractor(model_id=DEFAULT.model_id)
    backend = APBackend("data/records.json")
    seen = SeenStore()

    if args.all:
        pdfs = sorted(Path("data/invoices").glob("*.pdf"))
    elif args.pdf:
        pdfs = [Path(args.pdf)]
    else:
        pdfs = [Path("data/invoices/INV-20273_math_error.pdf")]

    for p in pdfs:
        run_one(p, extractor, backend, seen)

    posts = sum(1 for x in backend.ledger if x["action"] == "post_to_erp")
    routes = sum(1 for x in backend.ledger if x["action"] == "route_for_approval")
    print(f"\nledger: {posts} posted to ERP, {routes} routed for approval")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
