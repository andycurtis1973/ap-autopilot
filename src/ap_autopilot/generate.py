"""Generate the sample document set: realistic vendor-invoice **PDFs** plus the
matching purchase orders and goods receipts, with a controlled mix of clean
invoices and planted exceptions (math error, price variance, short receipt,
missing PO, duplicate). Deterministic given a seed so the demo, tests, and video
all see the same documents.

Each Case carries the ground-truth Invoice (what a perfect extractor would
return) so the offline pipeline and unit suite never need Bedrock. The live
extractor reads the very same PDFs.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from .model import (Invoice, LineItem, PurchaseOrder, POLine, GoodsReceipt,
                    ReceiptLine, money, qty, AUTO_APPROVED, ROUTED, REJECTED)

# --- a small but believable supplier universe ------------------------------
VENDORS = [
    ("V-1001", "Northwind Office Supply", "210 Cedar St, Columbus, OH 43215"),
    ("V-1002", "Cascade IT Hardware",     "88 Marine Dr, Seattle, WA 98101"),
    ("V-1003", "Brightline Facilities",   "417 Oak Ave, Austin, TX 78701"),
    ("V-1004", "Meridian Packaging Co.",  "9 Lakeshore Blvd, Chicago, IL 60601"),
]

CATALOG = {
    "V-1001": [("OFF-PAPER-A4", "Premium A4 paper, 5-ream case", "42.50"),
               ("OFF-TONER-58X", "HP 58X toner cartridge", "118.00"),
               ("OFF-PEN-BLK", "Gel pens, black, box of 12", "9.75"),
               ("OFF-NOTE-YEL", "Sticky notes, 3x3, 12-pack", "14.20")],
    "V-1002": [("IT-SSD-1TB", "1TB NVMe SSD", "94.00"),
               ("IT-DOCK-USB4", "USB4 docking station", "189.00"),
               ("IT-CBL-HDMI", "HDMI 2.1 cable, 2m", "12.50"),
               ("IT-MON-27", "27in 1440p monitor", "228.00")],
    "V-1003": [("FAC-NITRILE", "Nitrile gloves, case of 1000", "78.00"),
               ("FAC-CLEAN-5G", "Industrial cleaner, 5 gal", "54.00"),
               ("FAC-TOWEL-BR", "Brown roll towels, 12-pack", "31.40")],
    "V-1004": [("PKG-BOX-M", "Corrugated box, medium, bundle/25", "27.00"),
               ("PKG-WRAP-18", "Stretch wrap, 18in roll", "22.50"),
               ("PKG-TAPE-CL", "Packing tape, clear, 6-pack", "16.80")],
}

TAX_RATE = Decimal("0.07")


@dataclass
class Case:
    label: str                 # clean | math_error | price_variance | short_receipt | missing_po | duplicate | unordered_item
    invoice: Invoice           # ground truth
    po: PurchaseOrder | None
    receipt: GoodsReceipt | None
    pdf_path: str
    expected_status: str
    expected_codes: list[str] = field(default_factory=list)


@dataclass
class DocSet:
    cases: list[Case]

    @property
    def pos(self) -> dict[str, PurchaseOrder]:
        return {c.po.po_number: c.po for c in self.cases if c.po}

    @property
    def receipts(self) -> dict[str, GoodsReceipt]:
        return {c.receipt.po_number: c.receipt for c in self.cases if c.receipt}

    def stp_target(self) -> float:
        clean = sum(1 for c in self.cases if c.expected_status == AUTO_APPROVED)
        return clean / len(self.cases) if self.cases else 0.0


# --- core builders ---------------------------------------------------------
def _build_clean(rng: random.Random, idx: int) -> Case:
    vid, vname, _ = VENDORS[idx % len(VENDORS)]
    items = rng.sample(CATALOG[vid], k=rng.randint(2, min(4, len(CATALOG[vid]))))
    po_no = f"PO-{4500 + idx}"
    inv_no = f"INV-{20260 + idx}"

    po_lines, inv_lines, rcpt_lines = [], [], []
    for sku, desc, price in items:
        q = qty(rng.randint(2, 12))
        up = money(price)
        po_lines.append(POLine(sku, desc, q, up))
        inv_lines.append(LineItem(sku, desc, q, up, money(q * up)))
        rcpt_lines.append(ReceiptLine(sku, q))

    subtotal = money(sum((li.amount for li in inv_lines), Decimal("0")))
    tax = money(subtotal * TAX_RATE)
    total = money(subtotal + tax)

    inv = Invoice(inv_no, vid, vname, f"2026-06-{(idx % 27) + 1:02d}", po_no, "USD",
                  inv_lines, subtotal, tax, total)
    po = PurchaseOrder(po_no, vid, vname, "USD", po_lines)
    rcpt = GoodsReceipt(f"GR-{7800 + idx}", po_no, f"2026-06-{(idx % 27) + 1:02d}", rcpt_lines)
    return Case("clean", inv, po, rcpt, "", AUTO_APPROVED, [])


def _mutate(case: Case, label: str, rng: random.Random) -> Case:
    """Turn a clean case into an exception case in-place (returns a new Case)."""
    inv, po, rcpt = case.invoice, case.po, case.receipt

    if label == "math_error":
        # Inflate the printed total by a few dollars — extension/total inconsistency.
        bad_total = money(inv.total + money(rng.choice(["8.40", "15.00", "23.75"])))
        inv.total = bad_total
        return Case(label, inv, po, rcpt, "", ROUTED, ["TOTAL_ERROR"])

    if label == "price_variance":
        # Vendor bills above the PO price on one line, beyond tolerance.
        li = inv.line_items[0]
        bumped = money(li.unit_price * Decimal("1.18"))  # +18%
        li.unit_price = bumped
        li.amount = money(li.quantity * bumped)
        inv.subtotal = money(sum((x.amount for x in inv.line_items), Decimal("0")))
        inv.tax = money(inv.subtotal * TAX_RATE)
        inv.total = money(inv.subtotal + inv.tax)
        return Case(label, inv, po, rcpt, "", ROUTED, ["PRICE_VARIANCE"])

    if label == "short_receipt":
        # Billed in full, but the warehouse only received part of one line.
        li = inv.line_items[0]
        short = qty(max(Decimal("1"), li.quantity - Decimal("2")))
        for rl in rcpt.lines:
            if rl.sku == li.sku:
                rl.quantity_received = short
        return Case(label, inv, po, rcpt, "", ROUTED, ["QUANTITY_NOT_RECEIVED"])

    if label == "missing_po":
        # Invoice references a PO that AP can't find (and we don't register it).
        inv.po_number = f"PO-{9000 + rng.randint(1, 99)}"
        return Case(label, inv, None, None, "", REJECTED, ["PO_NOT_FOUND"])

    if label == "unordered_item":
        # An item billed that isn't on the PO at all.
        extra = LineItem("OFF-MISC-X", "Unlisted handling fee", qty("1"),
                         money("48.00"), money("48.00"))
        inv.line_items.append(extra)
        inv.subtotal = money(sum((x.amount for x in inv.line_items), Decimal("0")))
        inv.tax = money(inv.subtotal * TAX_RATE)
        inv.total = money(inv.subtotal + inv.tax)
        return Case(label, inv, po, rcpt, "", ROUTED, ["UNORDERED_ITEM"])

    raise ValueError(f"unknown mutation {label}")


def build_docset(out_dir: str | Path, seed: int = 7, n_clean: int = 13,
                 exceptions: tuple[str, ...] = (
                     "math_error", "price_variance", "short_receipt",
                     "missing_po", "unordered_item", "duplicate"),
                 write_pdfs: bool = True) -> DocSet:
    """Build the full document set and (optionally) render the invoice PDFs."""
    out = Path(out_dir)
    inv_dir = out / "invoices"
    inv_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    cases: list[Case] = []
    total = n_clean + len([e for e in exceptions if e != "duplicate"])
    # Build the clean base cases first.
    for i in range(total):
        cases.append(_build_clean(rng, i))

    # Apply mutations to the tail cases (keep early ones clean for duplicate src).
    mut_targets = [e for e in exceptions if e != "duplicate"]
    for off, label in enumerate(mut_targets):
        ci = total - len(mut_targets) + off
        cases[ci] = _mutate(cases[ci], label, rng)

    # Duplicate: a verbatim re-send of an early clean invoice (same id+vendor).
    dup_case = None
    if "duplicate" in exceptions:
        src = cases[0]
        dup_inv = Invoice.from_dict(src.invoice.to_dict())
        dup_case = Case("duplicate", dup_inv, src.po, src.receipt, "",
                        REJECTED, ["DUPLICATE_INVOICE"])
        cases.append(dup_case)

    # Render PDFs (the duplicate reuses the source PDF).
    for i, c in enumerate(cases):
        if c.label == "duplicate":
            c.pdf_path = cases[0].pdf_path
            continue
        path = inv_dir / f"{c.invoice.invoice_id}_{c.label}.pdf"
        if write_pdfs:
            render_invoice_pdf(c.invoice, path)
        c.pdf_path = str(path)
    if dup_case is not None:
        dup_case.pdf_path = cases[0].pdf_path

    ds = DocSet(cases)
    _write_records(out, ds)
    return ds


def _write_records(out: Path, ds: DocSet) -> None:
    """Persist POs, receipts, and the expected outcomes as JSON (audit/inspection)."""
    records = {
        "purchase_orders": {po_no: {
            "po_number": po.po_number, "vendor_id": po.vendor_id,
            "vendor_name": po.vendor_name, "currency": po.currency,
            "lines": [{"sku": l.sku, "description": l.description,
                       "quantity_ordered": str(l.quantity_ordered),
                       "unit_price": str(l.unit_price)} for l in po.lines],
        } for po_no, po in ds.pos.items()},
        "goods_receipts": {po_no: {
            "receipt_id": r.receipt_id, "po_number": r.po_number,
            "received_date": r.received_date,
            "lines": [{"sku": l.sku, "quantity_received": str(l.quantity_received)}
                      for l in r.lines],
        } for po_no, r in ds.receipts.items()},
        "expected": [{"invoice_id": c.invoice.invoice_id, "label": c.label,
                      "pdf": Path(c.pdf_path).name if c.pdf_path else None,
                      "status": c.expected_status, "codes": c.expected_codes}
                     for c in ds.cases],
    }
    (out / "records.json").write_text(json.dumps(records, indent=2))


# --- PDF rendering ---------------------------------------------------------
def render_invoice_pdf(inv: Invoice, path: str | Path) -> None:
    """Draw a clean, realistic single-page vendor invoice."""
    vendor_addr = next((a for v, n, a in VENDORS if v == inv.vendor_id), "")
    c = canvas.Canvas(str(path), pagesize=LETTER)
    W, H = LETTER
    m = 0.9 * inch

    # Letterhead
    c.setFillColorRGB(0.10, 0.12, 0.16)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(m, H - m, inv.vendor_name)
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0.45, 0.47, 0.5)
    c.drawString(m, H - m - 16, vendor_addr)
    c.drawString(m, H - m - 28, f"Vendor ID: {inv.vendor_id}")

    c.setFillColorRGB(0.16, 0.55, 0.50)
    c.setFont("Helvetica-Bold", 26)
    c.drawRightString(W - m, H - m, "INVOICE")

    # Meta block
    c.setFillColorRGB(0.10, 0.12, 0.16)
    c.setFont("Helvetica", 10)
    y = H - m - 60
    meta = [("Invoice #", inv.invoice_id), ("Date", inv.invoice_date),
            ("PO Number", inv.po_number or "—"), ("Currency", inv.currency)]
    for label, val in meta:
        c.setFillColorRGB(0.45, 0.47, 0.5)
        c.drawRightString(W - m - 110, y, f"{label}:")
        c.setFillColorRGB(0.10, 0.12, 0.16)
        c.setFont("Helvetica-Bold", 10)
        c.drawRightString(W - m, y, str(val))
        c.setFont("Helvetica", 10)
        y -= 16

    # Bill-to
    c.setFillColorRGB(0.45, 0.47, 0.5)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(m, H - m - 60, "BILL TO")
    c.setFillColorRGB(0.10, 0.12, 0.16)
    c.setFont("Helvetica", 10)
    c.drawString(m, H - m - 76, "Globex Manufacturing, Inc.")
    c.drawString(m, H - m - 90, "Accounts Payable Dept.")
    c.drawString(m, H - m - 104, "500 Industrial Pkwy, Reno, NV 89501")

    # Line item table
    ty = H - m - 150
    cols = [m, m + 1.1 * inch, m + 4.1 * inch, m + 4.9 * inch, m + 5.9 * inch]
    headers = ["ITEM", "DESCRIPTION", "QTY", "UNIT", "AMOUNT"]
    c.setFillColorRGB(0.16, 0.55, 0.50)
    c.rect(m, ty - 4, W - 2 * m, 20, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 9)
    aligns = ["l", "l", "r", "r", "r"]
    rights = [None, None, m + 4.6 * inch, m + 5.6 * inch, W - m]
    for i, h in enumerate(headers):
        if aligns[i] == "l":
            c.drawString(cols[i] + 4, ty, h)
        else:
            c.drawRightString(rights[i], ty, h)

    ty -= 26
    c.setFont("Helvetica", 9)
    for n, li in enumerate(inv.line_items):
        if n % 2 == 1:
            c.setFillColorRGB(0.96, 0.97, 0.97)
            c.rect(m, ty - 5, W - 2 * m, 18, fill=1, stroke=0)
        c.setFillColorRGB(0.10, 0.12, 0.16)
        c.drawString(cols[0] + 4, ty, li.sku)
        c.drawString(cols[1] + 4, ty, li.description[:42])
        c.drawRightString(m + 4.6 * inch, ty, _num(li.quantity))
        c.drawRightString(m + 5.6 * inch, ty, f"{li.unit_price:,.2f}")
        c.drawRightString(W - m, ty, f"{li.amount:,.2f}")
        ty -= 18

    # Totals
    ty -= 12
    for label, val, bold in [("Subtotal", inv.subtotal, False),
                             (f"Tax ({TAX_RATE*100:.0f}%)", inv.tax, False),
                             ("TOTAL", inv.total, True)]:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 11 if bold else 10)
        c.setFillColorRGB(0.10, 0.12, 0.16) if bold else c.setFillColorRGB(0.45, 0.47, 0.5)
        c.drawRightString(m + 5.6 * inch, ty, f"{label}:")
        c.setFillColorRGB(0.10, 0.12, 0.16)
        c.drawRightString(W - m, ty, f"{inv.currency} {val:,.2f}")
        ty -= 18

    c.setFillColorRGB(0.6, 0.62, 0.65)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(m, m, "Payment terms: Net 30. Remit per master service agreement. "
                       "Questions: ap@" + inv.vendor_name.split()[0].lower() + ".example")
    c.showPage()
    c.save()


def _num(d: Decimal) -> str:
    """Whole numbers without trailing zeros; fractional shown to 3dp."""
    return str(int(d)) if d == d.to_integral_value() else f"{d:.3f}"
