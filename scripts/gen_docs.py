#!/usr/bin/env python3
"""Generate the sample document set (invoice PDFs + PO/receipt records).

    PYTHONPATH=src python3 scripts/gen_docs.py            # -> data/
    PYTHONPATH=src python3 scripts/gen_docs.py --out data --seed 7
"""

import argparse
from pathlib import Path

from ap_autopilot.generate import build_docset


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--clean", type=int, default=13, help="number of clean invoices")
    args = ap.parse_args()

    ds = build_docset(args.out, seed=args.seed, n_clean=args.clean)
    out = Path(args.out)
    pdfs = sorted({c.pdf_path for c in ds.cases})
    print(f"wrote {len(pdfs)} invoice PDFs to {out / 'invoices'}")
    print(f"wrote {len(ds.pos)} POs + {len(ds.receipts)} goods receipts to {out / 'records.json'}")
    print(f"cases: {len(ds.cases)}  |  STP target {ds.stp_target()*100:.0f}%")
    by_label: dict[str, int] = {}
    for c in ds.cases:
        by_label[c.label] = by_label.get(c.label, 0) + 1
    print("mix:", ", ".join(f"{k}={v}" for k, v in sorted(by_label.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
