#!/usr/bin/env python3
"""Extract one (or all) invoice PDFs with real Bedrock vision and score the
result against ground truth. This is the live counterpart to the offline
pipeline — it proves Claude reads the actual PDFs correctly.

    PYTHONPATH=src python3 scripts/run_extract.py                       # all PDFs (Sonnet 4.6)
    PYTHONPATH=src python3 scripts/run_extract.py data/invoices/INV-20273_math_error.pdf
    PYTHONPATH=src python3 scripts/run_extract.py --model haiku --max-spend 0.25

Spends real money (a few cents). A --max-spend cap aborts before exceeding it.
"""

import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _awsclock import ensure_clock_synced  # noqa: E402

from ap_autopilot.generate import build_docset  # noqa: E402
from ap_autopilot.pageimage import pdf_to_pngs  # noqa: E402
from ap_autopilot.extract import BedrockVisionExtractor  # noqa: E402
from ap_autopilot.bedrock_models import BY_NAME, DEFAULT  # noqa: E402


def _score(truth, got) -> tuple[int, int, list[str]]:
    """Field-level accuracy of an extraction vs ground truth."""
    checks = {
        "invoice_id": truth.invoice_id == got.invoice_id,
        "vendor_name": truth.vendor_name.lower() in got.vendor_name.lower()
        or got.vendor_name.lower() in truth.vendor_name.lower(),
        "po_number": (truth.po_number or "") == (got.po_number or ""),
        "subtotal": truth.subtotal == got.subtotal,
        "tax": truth.tax == got.tax,
        "total": truth.total == got.total,
        "n_lines": len(truth.line_items) == len(got.line_items),
    }
    misses = [k for k, ok in checks.items() if not ok]
    return sum(checks.values()), len(checks), misses


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="?", help="a single PDF; default = whole set")
    ap.add_argument("--model", default="sonnet", choices=list(BY_NAME))
    ap.add_argument("--max-spend", type=float, default=0.50)
    args = ap.parse_args()

    ensure_clock_synced("us-east-1")
    pricing = BY_NAME[args.model]
    ex = BedrockVisionExtractor(model_id=pricing.model_id)

    # Ground truth keyed by pdf path (regenerate deterministically; no re-render).
    ds = build_docset("data", write_pdfs=False)
    truth_by_pdf = {Path(c.pdf_path).name: c.invoice for c in ds.cases}

    pdfs = [Path(args.pdf)] if args.pdf else sorted(Path("data/invoices").glob("*.pdf"))
    spent = Decimal("0")
    tot_correct = tot_checks = 0
    print(f"model: {pricing.model_id}\n")
    for p in pdfs:
        if float(spent) > args.max_spend:
            print(f"… stopping: spend ${spent:.4f} hit cap ${args.max_spend}")
            break
        res = ex.extract(pdf_to_pngs(p))
        spent += res.call_cost(pricing)
        truth = truth_by_pdf.get(p.name)
        if truth:
            c, n, misses = _score(truth, res.invoice)
            tot_correct += c
            tot_checks += n
            flag = "✅" if not misses else "⚠ " + ",".join(misses)
            print(f"{p.name:<34} {res.invoice.invoice_id:<11} "
                  f"total={res.invoice.total:>10} {c}/{n} {flag}")
        else:
            print(f"{p.name:<34} {res.invoice.invoice_id} total={res.invoice.total}")

    print(f"\nfield accuracy: {tot_correct}/{tot_checks} "
          f"({100*tot_correct/max(1,tot_checks):.1f}%)   spend: ${spent:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
