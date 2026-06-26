from ap_autopilot.model import (Invoice, LineItem, PurchaseOrder, POLine,
                                GoodsReceipt, ReceiptLine, money, qty)
from ap_autopilot.match import three_way_match


def _setup(inv_price="5.00", inv_qty="2", recv_qty="2", po_vendor="V-1"):
    inv = Invoice("INV-1", "V-1", "Acme", "2026-01-01", "PO-1", "USD",
                  [LineItem("A", "a", qty(inv_qty), money(inv_price),
                            money(qty(inv_qty) * money(inv_price)))],
                  money(qty(inv_qty) * money(inv_price)), money("0"),
                  money(qty(inv_qty) * money(inv_price)))
    po = PurchaseOrder("PO-1", po_vendor, "Acme", "USD",
                       [POLine("A", "a", qty("2"), money("5.00"))])
    rcpt = GoodsReceipt("GR-1", "PO-1", "2026-01-01", [ReceiptLine("A", qty(recv_qty))])
    return inv, po, rcpt


def test_clean_match_no_exceptions():
    inv, po, rcpt = _setup()
    assert three_way_match(inv, po, rcpt) == []


def test_missing_po_is_blocker():
    inv, po, rcpt = _setup()
    codes = [(e.code, e.severity) for e in three_way_match(inv, None, rcpt)]
    assert ("PO_NOT_FOUND", "blocker") in codes


def test_price_variance_flagged():
    inv, po, rcpt = _setup(inv_price="6.50")  # +$1.50 over PO 5.00, beyond $1/2% band
    codes = [e.code for e in three_way_match(inv, po, rcpt)]
    assert "PRICE_VARIANCE" in codes


def test_price_within_tolerance_ok():
    inv, po, rcpt = _setup(inv_price="5.05")  # +1%, under 2%/$1
    assert [e.code for e in three_way_match(inv, po, rcpt)] == []


def test_short_receipt_flagged():
    inv, po, rcpt = _setup(recv_qty="1")  # billed 2, received 1
    codes = [e.code for e in three_way_match(inv, po, rcpt)]
    assert "QUANTITY_NOT_RECEIVED" in codes


def test_no_receipt_makes_quantity_blocker():
    inv, po, _ = _setup()
    codes = [(e.code, e.severity) for e in three_way_match(inv, po, None)]
    assert ("QUANTITY_NOT_RECEIVED", "blocker") in codes


def test_vendor_mismatch_blocker():
    inv, po, rcpt = _setup(po_vendor="V-9")
    codes = [(e.code, e.severity) for e in three_way_match(inv, po, rcpt)]
    assert ("VENDOR_MISMATCH", "blocker") in codes
