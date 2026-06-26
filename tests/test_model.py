from decimal import Decimal

from ap_autopilot.model import Invoice, LineItem, money, qty


def test_money_quantizes_and_is_lossless_from_str():
    assert money("12.345") == Decimal("12.35")   # half-up
    assert money(12.1) == Decimal("12.10")
    assert money("1000") == Decimal("1000.00")


def test_line_computed_amount():
    li = LineItem("X", "thing", qty("3"), money("2.50"), money("7.50"))
    assert li.computed_amount == Decimal("7.50")


def test_invoice_round_trip():
    inv = Invoice("INV-1", "V-1", "Acme", "2026-01-01", "PO-1", "USD",
                  [LineItem("A", "a", qty("2"), money("5.00"), money("10.00"))],
                  money("10.00"), money("0.70"), money("10.70"))
    inv2 = Invoice.from_dict(inv.to_dict())
    assert inv2.to_dict() == inv.to_dict()
    assert inv2.computed_subtotal == Decimal("10.00")


def test_po_number_none_round_trips():
    inv = Invoice("INV-2", "V-1", "Acme", "2026-01-01", None, "USD", [],
                  money("0"), money("0"), money("0"))
    assert Invoice.from_dict(inv.to_dict()).po_number is None
