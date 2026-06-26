from ap_autopilot.generate import build_docset
from ap_autopilot.pipeline import decide, SeenStore
from ap_autopilot.model import AUTO_APPROVED, ROUTED, REJECTED


def test_docset_cases_match_their_expectations(tmp_path):
    """The headline behavior: every planted case decides as designed."""
    ds = build_docset(tmp_path, write_pdfs=False)
    seen = SeenStore()
    for c in ds.cases:
        d = decide(c.invoice, ds.pos.get(c.invoice.po_number),
                   ds.receipts.get(c.invoice.po_number), seen)
        got = sorted(e.code for e in d.exceptions)
        assert d.status == c.expected_status, f"{c.invoice.invoice_id} ({c.label}): {got}"
        for code in c.expected_codes:
            assert code in got, f"{c.invoice.invoice_id}: missing {code} in {got}"


def test_duplicate_only_after_first_seen(tmp_path):
    ds = build_docset(tmp_path, write_pdfs=False)
    seen = SeenStore()
    first = ds.cases[0]
    d1 = decide(first.invoice, ds.pos.get(first.invoice.po_number),
                ds.receipts.get(first.invoice.po_number), seen)
    assert d1.status == AUTO_APPROVED  # first time through is clean
    d2 = decide(first.invoice, ds.pos.get(first.invoice.po_number),
                ds.receipts.get(first.invoice.po_number), seen)
    assert d2.status == REJECTED
    assert any(e.code == "DUPLICATE_INVOICE" for e in d2.exceptions)


def test_stp_rate_in_expected_band(tmp_path):
    ds = build_docset(tmp_path, write_pdfs=False)
    seen = SeenStore()
    auto = sum(1 for c in ds.cases
               if decide(c.invoice, ds.pos.get(c.invoice.po_number),
                         ds.receipts.get(c.invoice.po_number), seen).status == AUTO_APPROVED)
    rate = auto / len(ds.cases)
    assert 0.5 <= rate <= 0.85
