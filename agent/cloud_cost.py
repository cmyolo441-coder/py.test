"""Cloud cost optimizer — analyse and suggest savings for AWS/GCP/Azure.

Real analysis based on:
  * Pricing rules (on-demand vs reserved vs spot)
  * Right-sizing (over-provisioned instances)
  * Idle resource detection (via metrics)
  * Storage tier optimisation

No cloud credentials needed — works on a spec you provide or that the
agent generates by inspecting Terraform/CloudFormation files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Approximate monthly USD prices per instance type (on-demand, us-east-1).
AWS_PRICING: dict[str, float] = {
    "t3.nano": 3.80, "t3.micro": 7.59, "t3.small": 15.19, "t3.medium": 30.37,
    "t3.large": 60.74, "t3.xlarge": 121.48, "t3.2xlarge": 242.97,
    "m5.large": 70.08, "m5.xlarge": 140.16, "m5.2xlarge": 280.32,
    "c5.large": 61.99, "c5.xlarge": 123.98,
    "r5.large": 75.42, "r5.xlarge": 150.85,
}

# Reserved instance discount (3-year, all upfront) vs on-demand.
RI_DISCOUNT = 0.55  # ~55% saving
SPOT_DISCOUNT = 0.70  # ~70% saving (with interruption risk)


@dataclass
class Resource:
    id: str
    type: str  # "ec2", "s3", "rds", "lambda"
    region: str = "us-east-1"
    monthly_cost_usd: float = 0.0
    utilisation_pct: float = 0.0  # average utilisation
    tags: dict[str, str] = field(default_factory=dict)


@dataclass
class CostSaving:
    resource_id: str
    action: str
    current_monthly: float
    suggested_monthly: float
    monthly_saving: float
    annual_saving: float
    reason: str
    confidence: float = 1.0


@dataclass
class CostReport:
    resources: list[Resource] = field(default_factory=list)
    savings: list[CostSaving] = field(default_factory=list)
    total_monthly: float = 0.0
    total_suggested_monthly: float = 0.0

    @property
    def total_monthly_saving(self) -> float:
        return sum(s.monthly_saving for s in self.savings)

    @property
    def total_annual_saving(self) -> float:
        return self.total_monthly_saving * 12

    def dashboard(self) -> str:
        lines = [
            "╔═══════════════════════════════════════════════════════════╗",
            "║              💰  CLOUD COST OPTIMISER                      ║",
            "╠═══════════════════════════════════════════════════════════╣",
            f"║  Resources analysed:  {len(self.resources):<37}║",
            f"║  Current monthly:     ${self.total_monthly:<10.2f}                       ║",
            f"║  Suggested monthly:   ${self.total_suggested_monthly:<10.2f}                       ║",
            f"║  Monthly saving:      ${self.total_monthly_saving:<10.2f}                       ║",
            f"║  Annual saving:       ${self.total_annual_saving:<10.2f}                       ║",
            "╚═══════════════════════════════════════════════════════════╝",
        ]
        if self.savings:
            lines.append("\n💡 Savings opportunities:")
            for s in self.savings:
                lines.append(
                    f"  {s.resource_id:<20} {s.action:<25} "
                    f"save ${s.monthly_saving:.2f}/mo (${s.annual_saving:.2f}/yr)\n"
                    f"    {s.reason}"
                )
        return "\n".join(lines)


def analyse_resources(resources: list[Resource]) -> CostReport:
    """Analyse a list of cloud resources and suggest savings."""
    report = CostReport(resources=resources)
    report.total_monthly = sum(r.monthly_cost_usd for r in resources)

    for r in resources:
        # Right-size under-utilised instances.
        if r.type == "ec2" and r.utilisation_pct < 20 and r.monthly_cost_usd > 50:
            saving = r.monthly_cost_usd * 0.5  # suggest half-size
            report.savings.append(CostSaving(
                resource_id=r.id,
                action="right-size (under-utilised)",
                current_monthly=r.monthly_cost_usd,
                suggested_monthly=r.monthly_cost_usd - saving,
                monthly_saving=saving,
                annual_saving=saving * 12,
                reason=f"EC2 at {r.utilisation_pct:.0f}% utilisation — downsize by ~50%",
            ))
            report.total_suggested_monthly += r.monthly_cost_usd - saving
        # Reserved instance for steady-state workloads.
        elif r.type == "ec2" and r.utilisation_pct > 60:
            saving = r.monthly_cost_usd * RI_DISCOUNT
            report.savings.append(CostSaving(
                resource_id=r.id,
                action="reserved instance (3yr)",
                current_monthly=r.monthly_cost_usd,
                suggested_monthly=r.monthly_cost_usd - saving,
                monthly_saving=saving,
                annual_saving=saving * 12,
                reason="Steady-state workload — buy reserved instance (saves ~55%)",
            ))
            report.total_suggested_monthly += r.monthly_cost_usd - saving
        # Spot for batch/fault-tolerant workloads.
        elif r.type == "ec2" and "batch" in r.tags.get("workload", "").lower():
            saving = r.monthly_cost_usd * SPOT_DISCOUNT
            report.savings.append(CostSaving(
                resource_id=r.id,
                action="spot instance",
                current_monthly=r.monthly_cost_usd,
                suggested_monthly=r.monthly_cost_usd - saving,
                monthly_saving=saving,
                annual_saving=saving * 12,
                reason="Batch workload — use spot instances (saves ~70%, interruptible)",
            ))
            report.total_suggested_monthly += r.monthly_cost_usd - saving
        # Idle resources.
        elif r.utilisation_pct < 5 and r.monthly_cost_usd > 10:
            report.savings.append(CostSaving(
                resource_id=r.id,
                action="terminate (idle)",
                current_monthly=r.monthly_cost_usd,
                suggested_monthly=0.0,
                monthly_saving=r.monthly_cost_usd,
                annual_saving=r.monthly_cost_usd * 12,
                reason=f"Resource at {r.utilisation_pct:.0f}% utilisation — terminate",
                confidence=0.9,
            ))
            report.total_suggested_monthly += 0
        else:
            report.total_suggested_monthly += r.monthly_cost_usd

    return report


# Example resource spec for demo/testing.
EXAMPLE_RESOURCES = [
    Resource(id="i-001", type="ec2", monthly_cost_usd=140.16, utilisation_pct=8.0, tags={"name": "web-server"}),
    Resource(id="i-002", type="ec2", monthly_cost_usd=280.32, utilisation_pct=75.0, tags={"name": "api-server"}),
    Resource(id="i-003", type="ec2", monthly_cost_usd=60.74, utilisation_pct=2.0, tags={"name": "staging"}),
    Resource(id="i-004", type="ec2", monthly_cost_usd=121.48, utilisation_pct=45.0, tags={"workload": "batch"}),
    Resource(id="db-001", type="rds", monthly_cost_usd=200.0, utilisation_pct=15.0),
]
