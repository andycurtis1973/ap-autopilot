"""Core data model for AP Autopilot.

These are the documents and records the agent reasons over. Money is carried as
Decimal everywhere — float arithmetic on currency is how you get a "1 cent off"
math-validation false positive, so we never use it. The dataclasses are plain
and JSON-round-trippable (see ``to_dict`` / ``from_dict``) because the same
records cross the AgentCore Gateway (as MCP tool I/O) and land in Memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def money(x: Any) -> Decimal:
    """Coerce to a 2-dp Decimal. Accepts str/int/float/Decimal.

    Strings are preferred (lossless). Floats are quantized to cents on the way
    in so an extractor returning 12.340000001 doesn't poison a comparison.
    """
    d = x if isinstance(x, Decimal) else Decimal(str(x))
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def qty(x: Any) -> Decimal:
    """Coerce a quantity to a 3-dp Decimal (units can be fractional, e.g. hours)."""
    d = x if isinstance(x, Decimal) else Decimal(str(x))
    return d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


@dataclass
class LineItem:
    """One billed/ordered/received line. ``sku`` is the join key across documents."""

    sku: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal  # extended = quantity * unit_price (as printed on the doc)

    def __post_init__(self) -> None:
        self.quantity = qty(self.quantity)
        self.unit_price = money(self.unit_price)
        self.amount = money(self.amount)

    @property
    def computed_amount(self) -> Decimal:
        """What the extension *should* be — used by math validation."""
        return money(self.quantity * self.unit_price)

    def to_dict(self) -> dict:
        return {"sku": self.sku, "description": self.description,
                "quantity": str(self.quantity), "unit_price": str(self.unit_price),
                "amount": str(self.amount)}

    @classmethod
    def from_dict(cls, d: dict) -> "LineItem":
        return cls(sku=str(d["sku"]), description=str(d.get("description", "")),
                   quantity=d["quantity"], unit_price=d["unit_price"], amount=d["amount"])


@dataclass
class Invoice:
    """A vendor invoice — the document the agent extracts from a PDF."""

    invoice_id: str
    vendor_id: str
    vendor_name: str
    invoice_date: str  # ISO yyyy-mm-dd
    po_number: str | None
    currency: str
    line_items: list[LineItem]
    subtotal: Decimal
    tax: Decimal
    total: Decimal

    def __post_init__(self) -> None:
        self.subtotal = money(self.subtotal)
        self.tax = money(self.tax)
        self.total = money(self.total)

    @property
    def computed_subtotal(self) -> Decimal:
        return money(sum((li.amount for li in self.line_items), Decimal("0")))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["line_items"] = [li.to_dict() for li in self.line_items]
        for k in ("subtotal", "tax", "total"):
            d[k] = str(getattr(self, k))
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Invoice":
        return cls(
            invoice_id=str(d["invoice_id"]), vendor_id=str(d.get("vendor_id", "")),
            vendor_name=str(d["vendor_name"]),
            invoice_date=str(d.get("invoice_date", "")),
            po_number=(str(d["po_number"]) if d.get("po_number") else None),
            currency=str(d.get("currency", "USD")),
            line_items=[LineItem.from_dict(li) for li in d.get("line_items", [])],
            subtotal=d.get("subtotal", 0), tax=d.get("tax", 0), total=d.get("total", 0))


@dataclass
class POLine:
    sku: str
    description: str
    quantity_ordered: Decimal
    unit_price: Decimal

    def __post_init__(self) -> None:
        self.quantity_ordered = qty(self.quantity_ordered)
        self.unit_price = money(self.unit_price)


@dataclass
class PurchaseOrder:
    """The authorizing purchase order — the contracted price/quantity."""

    po_number: str
    vendor_id: str
    vendor_name: str
    currency: str
    lines: list[POLine]

    @property
    def total(self) -> Decimal:
        return money(sum((l.quantity_ordered * l.unit_price for l in self.lines), Decimal("0")))

    def line_by_sku(self, sku: str) -> POLine | None:
        return next((l for l in self.lines if l.sku == sku), None)


@dataclass
class ReceiptLine:
    sku: str
    quantity_received: Decimal

    def __post_init__(self) -> None:
        self.quantity_received = qty(self.quantity_received)


@dataclass
class GoodsReceipt:
    """Proof the goods/services actually arrived — the third leg of the match."""

    receipt_id: str
    po_number: str
    received_date: str
    lines: list[ReceiptLine]

    def received_by_sku(self, sku: str) -> Decimal:
        return qty(sum((l.quantity_received for l in self.lines if l.sku == sku), Decimal("0")))


# --- exception taxonomy ----------------------------------------------------
# Severity drives the decision: any BLOCKER => reject; any REVIEW => route to a
# human; otherwise straight-through auto-approve. Codes are stable strings so
# they can be aggregated in metrics and shown in the demo/video.
SEVERITY_BLOCKER = "blocker"
SEVERITY_REVIEW = "review"


@dataclass
class Exception_:
    code: str
    severity: str          # SEVERITY_BLOCKER | SEVERITY_REVIEW
    message: str
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "message": self.message, "detail": self.detail}


# Decision statuses
AUTO_APPROVED = "auto_approved"
ROUTED = "routed_for_approval"
REJECTED = "rejected"


@dataclass
class Decision:
    """The agent's verdict on one invoice."""

    invoice_id: str
    status: str
    exceptions: list[Exception_] = field(default_factory=list)
    matched: bool = False           # passed 3-way match cleanly
    extracted: Invoice | None = None

    def to_dict(self) -> dict:
        return {"invoice_id": self.invoice_id, "status": self.status,
                "matched": self.matched,
                "exceptions": [e.to_dict() for e in self.exceptions],
                "extracted": self.extracted.to_dict() if self.extracted else None}
