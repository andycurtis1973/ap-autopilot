#!/usr/bin/env python3
"""Run the full decision pipeline over the sample document set OFFLINE — no
Bedrock, no AWS, no spend. Uses each case's ground-truth extraction so you can
see extract → validate → 3-way match → decide end to end, and confirm the
planted exceptions are caught.

    PYTHONPATH=src python3 scripts/run_offline.py
"""

import argparse

from ap_autopilot.generate import build_docset
from ap_autopilot.pipeline import decide, SeenStore
from ap_autopilot.metrics import RunMetrics, project_savings
from ap_autopilot.model import AUTO_APPROVED, ROUTED, REJECTED

ICON = {AUTO_APPROVED: "✅ auto", ROUTED: "🟡 review", REJECTED: "⛔ reject"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--volume", type=int, default=100_000, help="invoices/month for savings projection")
    args = ap.parse_args()

    ds = build_docset(args.out, seed=args.seed, write_pdfs=False)
    seen = SeenStore()
    m = RunMetrics()

    print(f"{'INVOICE':<14}{'VENDOR':<26}{'DECISION':<12}EXCEPTIONS")
    print("-" * 78)
    ok = 0
    for c in ds.cases:
        d = decide(c.invoice, ds.pos.get(c.invoice.po_number),
                   ds.receipts.get(c.invoice.po_number), seen)
        m.add(d)
        codes = ",".join(e.code for e in d.exceptions) or "—"
        print(f"{c.invoice.invoice_id:<14}{c.invoice.vendor_name[:24]:<26}"
              f"{ICON[d.status]:<12}{codes}")
        got = sorted(e.code for e in d.exceptions)
        want = sorted(c.expected_codes)
        # expected_codes is the *headline* exception; check it's present.
        if d.status == c.expected_status and all(w in got for w in want):
            ok += 1
        else:
            print(f"   ⚠ expected {c.expected_status}/{want}, got {d.status}/{got}")

    print("-" * 78)
    s = m.summary()
    print(f"STP rate: {s['stp_rate']*100:.0f}%   "
          f"auto={s['auto_approved']} review={s['routed_for_approval']} reject={s['rejected']}")
    print(f"exceptions: {s['exceptions']}")
    print(f"self-check: {ok}/{len(ds.cases)} cases matched expectation")

    proj = project_savings(m.stp_rate, invoices_per_month=args.volume)
    print(f"\nAt {args.volume:,}/mo: baseline ${proj['baseline_usd_per_year']:,.0f}/yr  ->  "
          f"with agent ${proj['with_agent_usd_per_year']:,.0f}/yr  "
          f"(saved ${proj['saved_usd_per_year']:,.0f}/yr)")
    return 0 if ok == len(ds.cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
