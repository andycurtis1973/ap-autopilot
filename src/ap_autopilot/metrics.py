"""Run-level metrics: straight-through-processing rate, exception mix, cost, and
latency. This is the payoff the demo and video report — the agent's value is the
fraction of invoices it clears end-to-end without a human, and the labor + cycle
time that removes.

``project_savings`` turns a measured STP rate into an annual figure at a given
invoice volume, using a manual-handling-cost assumption (industry benchmarks put
fully-loaded manual AP processing at ~$10-15 per invoice; we default to $11).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .model import Decision, AUTO_APPROVED, ROUTED, REJECTED


@dataclass
class RunMetrics:
    n: int = 0
    auto_approved: int = 0
    routed: int = 0
    rejected: int = 0
    exception_counts: dict[str, int] = field(default_factory=dict)
    total_cost: Decimal = Decimal("0")          # Bedrock extraction $
    extract_ms: list[float] = field(default_factory=list)
    decide_ms: list[float] = field(default_factory=list)

    def add(self, decision: Decision, cost: Decimal = Decimal("0"),
            extract_ms: float = 0.0, decide_ms: float = 0.0) -> None:
        self.n += 1
        if decision.status == AUTO_APPROVED:
            self.auto_approved += 1
        elif decision.status == ROUTED:
            self.routed += 1
        elif decision.status == REJECTED:
            self.rejected += 1
        for e in decision.exceptions:
            self.exception_counts[e.code] = self.exception_counts.get(e.code, 0) + 1
        self.total_cost += cost
        if extract_ms:
            self.extract_ms.append(extract_ms)
        if decide_ms:
            self.decide_ms.append(decide_ms)

    @property
    def stp_rate(self) -> float:
        return self.auto_approved / self.n if self.n else 0.0

    @property
    def cost_per_invoice(self) -> Decimal:
        return (self.total_cost / self.n) if self.n else Decimal("0")

    def _p(self, xs: list[float], pct: float) -> float:
        if not xs:
            return 0.0
        s = sorted(xs)
        return s[min(len(s) - 1, int(pct * len(s)))]

    def summary(self) -> dict:
        return {
            "invoices": self.n,
            "auto_approved": self.auto_approved,
            "routed_for_approval": self.routed,
            "rejected": self.rejected,
            "stp_rate": round(self.stp_rate, 4),
            "exceptions": dict(sorted(self.exception_counts.items())),
            "extraction_cost_usd": float(round(self.total_cost, 4)),
            "cost_per_invoice_usd": float(round(self.cost_per_invoice, 5)),
            "extract_ms_p50": round(self._p(self.extract_ms, 0.5), 1),
            "decide_ms_p50": round(self._p(self.decide_ms, 0.5), 3),
        }


def project_savings(stp_rate: float, invoices_per_month: int = 100_000,
                    manual_cost_per_invoice: Decimal = Decimal("11.00"),
                    touchless_cost_per_invoice: Decimal = Decimal("0.40")) -> dict:
    """Annual labor savings from auto-clearing the STP fraction.

    Baseline: every invoice handled manually. With the agent: the STP fraction
    costs only the touchless rate (compute + a clerk's glance at the audit log);
    the remainder still needs a human, but arrives pre-extracted and pre-matched.
    """
    annual = Decimal(invoices_per_month) * 12
    stp = Decimal(str(stp_rate))
    baseline = annual * manual_cost_per_invoice
    auto = annual * stp * touchless_cost_per_invoice
    remainder = annual * (Decimal("1") - stp) * manual_cost_per_invoice
    with_agent = auto + remainder
    return {
        "invoices_per_year": int(annual),
        "stp_rate": round(stp_rate, 4),
        "baseline_usd_per_year": float(round(baseline, 0)),
        "with_agent_usd_per_year": float(round(with_agent, 0)),
        "saved_usd_per_year": float(round(baseline - with_agent, 0)),
    }
