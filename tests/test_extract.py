"""Extractor tests with a fake Converse client — never calls Bedrock."""
import json

from ap_autopilot.extract import BedrockVisionExtractor, _parse_json, _normalize


class FakeBedrock:
    def __init__(self, reply: str, usage=None):
        self._reply = reply
        self._usage = usage or {"inputTokens": 1400, "outputTokens": 220}
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"content": [{"text": self._reply}]}},
                "usage": self._usage}


GOOD = {
    "invoice_id": "INV-9", "vendor_id": "V-1", "vendor_name": "Acme",
    "invoice_date": "2026-02-02", "po_number": "PO-9", "currency": "USD",
    "line_items": [{"sku": "A", "description": "a", "quantity": 2,
                    "unit_price": 5, "amount": 10}],
    "subtotal": 10, "tax": 0.7, "total": 10.7,
}


def test_parse_json_tolerates_fence():
    txt = "```json\n" + json.dumps(GOOD) + "\n```"
    assert _parse_json(txt)["invoice_id"] == "INV-9"


def test_normalize_handles_nulls():
    d = _normalize({"invoice_id": "X", "vendor_name": "Y", "po_number": "null",
                    "line_items": [{"quantity": None, "unit_price": "", "amount": 5}],
                    "subtotal": None, "tax": None, "total": 5})
    assert d["po_number"] is None
    assert d["line_items"][0]["quantity"] == 0
    assert d["subtotal"] == 0


def test_extractor_returns_invoice_and_usage():
    fake = FakeBedrock(json.dumps(GOOD))
    ex = BedrockVisionExtractor(client=fake)
    res = ex.extract([b"\x89PNG-fake"])
    assert res.invoice.invoice_id == "INV-9"
    assert res.invoice.po_number == "PO-9"
    assert res.input_tokens == 1400 and res.output_tokens == 220
    # image was attached to the Converse content
    content = fake.calls[0]["messages"][0]["content"]
    assert any("image" in b for b in content)
    assert res.call_cost() > 0
