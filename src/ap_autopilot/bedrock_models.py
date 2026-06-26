"""Bedrock model ids + pricing, shared by the extractor and the cost metrics.

Prices are per-million tokens, us-region on-demand, June 2026 reference. Update
here in one place. Vision: an invoice page rendered at our DPI is ~1.2-1.6k
input tokens; the extractor reports real usage from the Converse response, so
these rates (not a token estimate) drive the cost figures in the demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ModelPricing:
    model_id: str
    input_per_m: Decimal   # $ per 1M input tokens
    output_per_m: Decimal  # $ per 1M output tokens


# Regional inference profiles (on-demand). Sonnet 4.6 is the extraction default:
# strong document vision + reliable JSON, best accuracy-for-cost on invoices.
SONNET = ModelPricing("us.anthropic.claude-sonnet-4-6",
                      Decimal("3.00"), Decimal("15.00"))
HAIKU = ModelPricing("us.anthropic.claude-haiku-4-5-20251001-v1:0",
                     Decimal("1.00"), Decimal("5.00"))
OPUS = ModelPricing("us.anthropic.claude-opus-4-8",
                    Decimal("15.00"), Decimal("75.00"))

BY_NAME = {"sonnet": SONNET, "haiku": HAIKU, "opus": OPUS}
DEFAULT = SONNET


def cost(pricing: ModelPricing, input_tokens: int, output_tokens: int) -> Decimal:
    """Dollar cost of one call from measured token usage."""
    return (pricing.input_per_m * Decimal(input_tokens)
            + pricing.output_per_m * Decimal(output_tokens)) / Decimal("1000000")
