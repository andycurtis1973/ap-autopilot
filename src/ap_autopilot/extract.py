"""Extract a structured Invoice from a rendered invoice page using Claude vision
on Bedrock (the Converse API). This is the only step that *reads the PDF*; the
rest of the pipeline is deterministic over the structured result.

Design for testability: ``BedrockVisionExtractor`` is the live path, but the
pipeline depends only on the ``Extractor`` protocol (``extract(images) ->
(Invoice, usage)``). The unit suite injects a ground-truth extractor, so
``pytest`` never calls Bedrock and never spends.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from .model import Invoice
from .bedrock_models import ModelPricing, DEFAULT, cost

# The schema we ask the model to fill. Kept terse and explicit — every field the
# downstream math/match needs, and nothing it doesn't.
EXTRACTION_PROMPT = """You are an accounts-payable document extractor. The image is one page of a
vendor invoice. Extract the fields below and return ONLY a JSON object, no prose, no markdown fence.

{
  "invoice_id": "the invoice number exactly as printed",
  "vendor_id": "vendor account/ID if shown, else \\"\\"",
  "vendor_name": "the vendor/supplier company name",
  "invoice_date": "ISO yyyy-mm-dd",
  "po_number": "the referenced purchase order number, or null if none",
  "currency": "ISO code, default USD",
  "line_items": [
    {"sku": "item/part code", "description": "text", "quantity": number,
     "unit_price": number, "amount": number}
  ],
  "subtotal": number,
  "tax": number,
  "total": number
}

Transcribe numbers EXACTLY as printed — do not recompute or "fix" them. If a value is absent, use null
(or "" for strings). Strip currency symbols and thousands separators from numbers."""


@dataclass
class ExtractResult:
    invoice: Invoice
    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str = ""

    def call_cost(self, pricing: ModelPricing = DEFAULT) -> Decimal:
        return cost(pricing, self.input_tokens, self.output_tokens)


class Extractor(Protocol):
    def extract(self, images: list[bytes]) -> ExtractResult: ...


def _parse_json(text: str) -> dict:
    """Pull the JSON object out of a model reply, tolerating an accidental fence."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in model reply: {text[:200]!r}")
    return json.loads(text[start:end + 1])


class BedrockVisionExtractor:
    """Live extractor: one Converse call per invoice, image(s) + the schema prompt."""

    def __init__(self, model_id: str = DEFAULT.model_id, region: str = "us-east-1",
                 client=None, max_tokens: int = 2000):
        self.model_id = model_id
        self.max_tokens = max_tokens
        if client is not None:
            self._client = client
        else:
            import boto3
            self._client = boto3.client("bedrock-runtime", region_name=region)

    def extract(self, images: list[bytes]) -> ExtractResult:
        content = [{"text": EXTRACTION_PROMPT}]
        for img in images:
            content.append({"image": {"format": "png", "source": {"bytes": img}}})
        resp = self._client.converse(
            modelId=self.model_id,
            messages=[{"role": "user", "content": content}],
            inferenceConfig={"maxTokens": self.max_tokens, "temperature": 0},
        )
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
        data = _parse_json(text)
        usage = resp.get("usage", {})
        return ExtractResult(
            invoice=Invoice.from_dict(_normalize(data)),
            input_tokens=int(usage.get("inputTokens", 0)),
            output_tokens=int(usage.get("outputTokens", 0)),
            model_id=self.model_id)


def _normalize(data: dict) -> dict:
    """Coerce a model's loose JSON into the shape Invoice.from_dict expects."""
    data.setdefault("vendor_id", "")
    data.setdefault("currency", "USD")
    if data.get("po_number") in ("", "null", "None"):
        data["po_number"] = None
    for li in data.get("line_items", []):
        li.setdefault("sku", "")
        li.setdefault("description", "")
        for k in ("quantity", "unit_price", "amount"):
            if li.get(k) in (None, ""):
                li[k] = 0
    for k in ("subtotal", "tax", "total"):
        if data.get(k) in (None, ""):
            data[k] = 0
    return data
