from ap_autopilot.model import Invoice, LineItem, money, qty
from ap_autopilot.validate import validate, validate_math


def _inv(subtotal, tax, total, lines):
    return Invoice("INV-1", "V-1", "Acme", "2026-01-01", "PO-1", "USD",
                   lines, money(subtotal), money(tax), money(total))


def test_clean_invoice_has_no_math_exceptions():
    inv = _inv("10.00", "0.70", "10.70",
               [LineItem("A", "a", qty("2"), money("5.00"), money("10.00"))])
    assert validate_math(inv) == []


def test_wrong_total_flagged():
    inv = _inv("10.00", "0.70", "99.99",
               [LineItem("A", "a", qty("2"), money("5.00"), money("10.00"))])
    codes = [e.code for e in validate_math(inv)]
    assert "TOTAL_ERROR" in codes


def test_line_extension_error_flagged():
    inv = _inv("10.00", "0.70", "10.70",
               [LineItem("A", "a", qty("2"), money("5.00"), money("12.00"))])
    codes = [e.code for e in validate_math(inv)]
    assert "LINE_EXTENSION_ERROR" in codes
    assert "SUBTOTAL_ERROR" in codes  # subtotal no longer equals sum of lines


def test_penny_rounding_is_tolerated():
    inv = _inv("10.00", "0.70", "10.71",  # 1 cent off — within tolerance
               [LineItem("A", "a", qty("2"), money("5.00"), money("10.00"))])
    assert validate_math(inv) == []


def test_missing_id_is_blocker():
    inv = _inv("0", "0", "0", [])
    inv.invoice_id = ""
    codes = [e.code for e in validate(inv)]
    assert "MISSING_INVOICE_ID" in codes
