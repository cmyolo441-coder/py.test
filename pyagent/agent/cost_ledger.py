"""
Cost Tracking Ledger

Per-unit token/cost tracking with persistent ledger and budget enforcement.
Based on GSD Pi's metrics tracking system.

Key features:
- UnitMetrics records with tokens (input, output, cache read/write)
- USD cost extraction from provider usage data
- Persistent ledger in .gsd/metrics.json
- Idempotency guards to prevent duplicate entries
- Budget enforcement with configurable actions (warn, pause, halt)
- Real-time dashboard display and cost projections
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
import threading


class BudgetAction(Enum):
    """Actions to take when budget is exceeded"""
    WARN = "warn"  # Just warn, continue execution
    PAUSE = "pause"  # Pause execution, require manual intervention
    HALT = "halt"  # Stop execution immediately


@dataclass
class UnitMetrics:
    """Metrics for a single unit execution"""
    unit_type: str  # e.g., "plan_slice", "execute_task", "validate_milestone"
    unit_id: str
    session_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    model: str = ""
    provider: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Calculate total tokens"""
        self.total_tokens = (
            self.input_tokens + 
            self.output_tokens + 
            self.cache_read_tokens + 
            self.cache_write_tokens
        )


@dataclass
class BudgetConfig:
    """Budget configuration"""
    enabled: bool = True
    max_cost_usd: Optional[float] = None
    max_tokens: Optional[int] = None
    warn_threshold_percent: float = 0.8  # Warn at 80% of budget
    action_on_exceed: BudgetAction = BudgetAction.WARN
    reset_period: Optional[str] = None  # e.g., "daily", "weekly"
    per_unit_limits: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class CostProjection:
    """Cost projection for future work"""
    estimated_remaining_units: int
    estimated_cost_per_unit: float
    estimated_total_cost: float
    confidence: str  # "high", "medium", "low"
    based_on_recent_units: int


class CostLedger:
    """
    Persistent cost tracking ledger with budget enforcement.
    
    Features:
    - Per-unit token and cost tracking
    - Persistent storage in .gsd/metrics.json
    - Idempotency guards to prevent duplicate entries
    - Budget enforcement with configurable actions
    - Cost projections and real-time monitoring
    """
    
    def __init__(self, project_root: str, budget_config: Optional[BudgetConfig] = None):
        self.project_root = Path(project_root).resolve()
        self.gsd_dir = self.project_root / ".gsd"
        self.metrics_file = self.gsd_dir / "metrics.json"
        
        self.budget_config = budget_config or BudgetConfig()
        
        # Thread-safe storage
        self._lock = threading.Lock()
        
        # Ensure directory exists
        self.gsd_dir.mkdir(parents=True, exist_ok=True)
        
        # Load existing metrics
        self._metrics: List[UnitMetrics] = []
        self._load_metrics()
        
        # Cost cache for idempotency
        self._recorded_units: set = set()
    
    def _load_metrics(self):
        """Load metrics from persistent storage"""
        if self.metrics_file.exists():
            try:
                with open(self.metrics_file, 'r') as f:
                    data = json.load(f)
                    self._metrics = [
                        UnitMetrics(**entry) for entry in data.get("units", [])
                    ]
                    self._recorded_units = set(data.get("recorded_units", []))
            except (json.JSONDecodeError, TypeError) as e:
                print(f"Warning: Failed to load metrics file: {e}")
                self._metrics = []
                self._recorded_units = set()
    
    def _save_metrics(self):
        """Save metrics to persistent storage"""
        with self._lock:
            data = {
                "units": [asdict(m) for m in self._metrics],
                "recorded_units": list(self._recorded_units),
                "last_updated": datetime.utcnow().isoformat(),
                "totals": self.get_totals().asdict()
            }
            
            # Atomic write
            temp_file = self.metrics_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            # Replace original file
            if self.metrics_file.exists():
                self.metrics_file.unlink()
            temp_file.rename(self.metrics_file)
    
    def _generate_unit_key(self, unit_type: str, unit_id: str, session_id: str) -> str:
        """Generate unique key for idempotency"""
        return f"{session_id}:{unit_type}:{unit_id}"
    
    def record_unit(
        self,
        unit_type: str,
        unit_id: str,
        session_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        cost_usd: float = 0.0,
        model: str = "",
        provider: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        force: bool = False
    ) -> bool:
        """
        Record metrics for a unit execution.
        
        Args:
            force: If True, bypass idempotency check and record anyway
            
        Returns:
            True if recorded, False if skipped due to idempotency
        """
        unit_key = self._generate_unit_key(unit_type, unit_id, session_id)
        
        with self._lock:
            # Idempotency check
            if not force and unit_key in self._recorded_units:
                return False
            
            # Check per-unit limits
            if not self._check_unit_limits(unit_type, input_tokens, output_tokens, cost_usd):
                return False
            
            # Create metrics record
            metrics = UnitMetrics(
                unit_type=unit_type,
                unit_id=unit_id,
                session_id=session_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                cost_usd=cost_usd,
                model=model,
                provider=provider,
                metadata=metadata or {}
            )
            
            self._metrics.append(metrics)
            self._recorded_units.add(unit_key)
            
            # Check budget enforcement
            self._check_budget_enforcement()
            
            # Save to disk
            self._save_metrics()
            
            return True
    
    def _check_unit_limits(
        self,
        unit_type: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float
    ) -> bool:
        """Check if unit exceeds per-unit limits"""
        if unit_type not in self.budget_config.per_unit_limits:
            return True
        
        limits = self.budget_config.per_unit_limits[unit_type]
        
        max_tokens = limits.get("max_tokens")
        if max_tokens and (input_tokens + output_tokens) > max_tokens:
            print(f"Warning: Unit {unit_type} exceeds token limit: {input_tokens + output_tokens} > {max_tokens}")
            if limits.get("enforce", False):
                return False
        
        max_cost = limits.get("max_cost_usd")
        if max_cost and cost_usd > max_cost:
            print(f"Warning: Unit {unit_type} exceeds cost limit: ${cost_usd:.4f} > ${max_cost:.4f}")
            if limits.get("enforce", False):
                return False
        
        return True
    
    def _check_budget_enforcement(self):
        """Check if budget limits are exceeded"""
        if not self.budget_config.enabled:
            return
        
        totals = self.get_totals()
        
        # Check cost budget
        if self.budget_config.max_cost_usd:
            cost_ratio = totals.total_cost_usd / self.budget_config.max_cost_usd
            
            if cost_ratio >= 1.0:
                # Budget exceeded
                action = self.budget_config.action_on_exceed
                if action == BudgetAction.WARN:
                    print(f"WARNING: Cost budget exceeded: ${totals.total_cost_usd:.2f} / ${self.budget_config.max_cost_usd:.2f}")
                elif action == BudgetAction.PAUSE:
                    raise BudgetExceededException(
                        f"Cost budget exceeded: ${totals.total_cost_usd:.2f} / ${self.budget_config.max_cost_usd:.2f}",
                        action="pause"
                    )
                elif action == BudgetAction.HALT:
                    raise BudgetExceededException(
                        f"Cost budget exceeded: ${totals.total_cost_usd:.2f} / ${self.budget_config.max_cost_usd:.2f}",
                        action="halt"
                    )
            elif cost_ratio >= self.budget_config.warn_threshold_percent:
                # Approaching budget
                print(f"Warning: Approaching cost budget: {cost_ratio:.1%} of ${self.budget_config.max_cost_usd:.2f}")
        
        # Check token budget
        if self.budget_config.max_tokens:
            token_ratio = totals.total_tokens / self.budget_config.max_tokens
            
            if token_ratio >= 1.0:
                action = self.budget_config.action_on_exceed
                if action == BudgetAction.WARN:
                    print(f"WARNING: Token budget exceeded: {totals.total_tokens} / {self.budget_config.max_tokens}")
                elif action == BudgetAction.PAUSE:
                    raise BudgetExceededException(
                        f"Token budget exceeded: {totals.total_tokens} / {self.budget_config.max_tokens}",
                        action="pause"
                    )
                elif action == BudgetAction.HALT:
                    raise BudgetExceededException(
                        f"Token budget exceeded: {totals.total_tokens} / {self.budget_config.max_tokens}",
                        action="halt"
                    )
            elif token_ratio >= self.budget_config.warn_threshold_percent:
                print(f"Warning: Approaching token budget: {token_ratio:.1%} of {self.budget_config.max_tokens}")
    
    def get_unit_metrics(self, unit_type: str, unit_id: str, session_id: str) -> Optional[UnitMetrics]:
        """Get metrics for a specific unit"""
        unit_key = self._generate_unit_key(unit_type, unit_id, session_id)
        
        for metrics in self._metrics:
            if self._generate_unit_key(metrics.unit_type, metrics.unit_id, metrics.session_id) == unit_key:
                return metrics
        
        return None
    
    def get_session_metrics(self, session_id: str) -> List[UnitMetrics]:
        """Get all metrics for a session"""
        return [m for m in self._metrics if m.session_id == session_id]
    
    def get_unit_type_metrics(self, unit_type: str) -> List[UnitMetrics]:
        """Get all metrics for a unit type"""
        return [m for m in self._metrics if m.unit_type == unit_type]
    
    def get_totals(self) -> 'CostTotals':
        """Calculate total costs and tokens across all units"""
        totals = CostTotals()
        
        for metrics in self._metrics:
            totals.input_tokens += metrics.input_tokens
            totals.output_tokens += metrics.output_tokens
            totals.cache_read_tokens += metrics.cache_read_tokens
            totals.cache_write_tokens += metrics.cache_write_tokens
            totals.total_tokens += metrics.total_tokens
            totals.total_cost_usd += metrics.cost_usd
            totals.unit_count += 1
        
        return totals
    
    def get_recent_totals(self, hours: int = 24) -> 'CostTotals':
        """Get totals for recent time period"""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        totals = CostTotals()
        
        for metrics in self._metrics:
            try:
                timestamp = datetime.fromisoformat(metrics.timestamp)
                if timestamp >= cutoff:
                    totals.input_tokens += metrics.input_tokens
                    totals.output_tokens += metrics.output_tokens
                    totals.cache_read_tokens += metrics.cache_read_tokens
                    totals.cache_write_tokens += metrics.cache_write_tokens
                    totals.total_tokens += metrics.total_tokens
                    totals.total_cost_usd += metrics.cost_usd
                    totals.unit_count += 1
            except ValueError:
                # Skip invalid timestamps
                continue
        
        return totals
    
    def project_costs(
        self,
        remaining_units: int,
        confidence: str = "medium"
    ) -> CostProjection:
        """
        Project costs for remaining work based on historical data.
        
        Args:
            remaining_units: Estimated number of remaining units
            confidence: Confidence level ("high", "medium", "low")
        """
        totals = self.get_totals()
        
        if totals.unit_count == 0:
            # No historical data, use defaults
            return CostProjection(
                estimated_remaining_units=remaining_units,
                estimated_cost_per_unit=0.1,  # Default assumption
                estimated_total_cost=remaining_units * 0.1,
                confidence="low",
                based_on_recent_units=0
            )
        
        # Calculate average cost per unit
        avg_cost_per_unit = totals.total_cost_usd / totals.unit_count
        
        # Use recent data for better accuracy if available
        recent_totals = self.get_recent_totals(hours=24)
        if recent_totals.unit_count >= 5:
            avg_cost_per_unit = recent_totals.total_cost_usd / recent_totals.unit_count
            based_on = recent_totals.unit_count
        else:
            based_on = totals.unit_count
        
        estimated_total_cost = remaining_units * avg_cost_per_unit
        
        return CostProjection(
            estimated_remaining_units=remaining_units,
            estimated_cost_per_unit=avg_cost_per_unit,
            estimated_total_cost=estimated_total_cost,
            confidence=confidence,
            based_on_recent_units=based_on
        )
    
    def get_cost_breakdown(self) -> Dict[str, Any]:
        """Get detailed cost breakdown by unit type and provider"""
        breakdown = {
            "by_unit_type": {},
            "by_provider": {},
            "by_model": {},
            "cache_efficiency": {}
        }
        
        for metrics in self._metrics:
            # By unit type
            if metrics.unit_type not in breakdown["by_unit_type"]:
                breakdown["by_unit_type"][metrics.unit_type] = {
                    "count": 0,
                    "total_cost": 0.0,
                    "total_tokens": 0
                }
            breakdown["by_unit_type"][metrics.unit_type]["count"] += 1
            breakdown["by_unit_type"][metrics.unit_type]["total_cost"] += metrics.cost_usd
            breakdown["by_unit_type"][metrics.unit_type]["total_tokens"] += metrics.total_tokens
            
            # By provider
            if metrics.provider:
                if metrics.provider not in breakdown["by_provider"]:
                    breakdown["by_provider"][metrics.provider] = {
                        "count": 0,
                        "total_cost": 0.0,
                        "total_tokens": 0
                    }
                breakdown["by_provider"][metrics.provider]["count"] += 1
                breakdown["by_provider"][metrics.provider]["total_cost"] += metrics.cost_usd
                breakdown["by_provider"][metrics.provider]["total_tokens"] += metrics.total_tokens
            
            # By model
            if metrics.model:
                if metrics.model not in breakdown["by_model"]:
                    breakdown["by_model"][metrics.model] = {
                        "count": 0,
                        "total_cost": 0.0,
                        "total_tokens": 0
                    }
                breakdown["by_model"][metrics.model]["count"] += 1
                breakdown["by_model"][metrics.model]["total_cost"] += metrics.cost_usd
                breakdown["by_model"][metrics.model]["total_tokens"] += metrics.total_tokens
        
        # Calculate cache efficiency
        totals = self.get_totals()
        if totals.total_tokens > 0:
            breakdown["cache_efficiency"] = {
                "cache_hit_rate": totals.cache_read_tokens / totals.total_tokens,
                "cache_write_rate": totals.cache_write_tokens / totals.total_tokens,
                "cache_savings_tokens": totals.cache_read_tokens,
                "cache_savings_usd": self._estimate_cache_savings(totals.cache_read_tokens)
            }
        
        return breakdown
    
    def _estimate_cache_savings(self, cache_read_tokens: int) -> float:
        """Estimate cost savings from cache hits"""
        # Assume cache reads cost 10% of regular tokens
        # This is a rough estimate, actual savings depend on provider
        estimated_full_cost = cache_read_tokens * 0.00001  # Rough estimate
        estimated_cache_cost = estimated_full_cost * 0.1
        return estimated_full_cost - estimated_cache_cost
    
    def reset_metrics(self, older_than_hours: Optional[int] = None):
        """Reset metrics, optionally only those older than specified hours"""
        with self._lock:
            if older_than_hours is None:
                # Reset all
                self._metrics = []
                self._recorded_units = set()
            else:
                # Reset only old metrics
                cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)
                self._metrics = [
                    m for m in self._metrics
                    if datetime.fromisoformat(m.timestamp) >= cutoff
                ]
                # Rebuild recorded units from remaining metrics
                self._recorded_units = {
                    self._generate_unit_key(m.unit_type, m.unit_id, m.session_id)
                    for m in self._metrics
                }
            
            self._save_metrics()
    
    def export_metrics(self, output_path: Optional[Path] = None) -> str:
        """Export metrics to JSON file"""
        if output_path is None:
            output_path = self.metrics_file.with_suffix('.export.json')
        
        data = {
            "export_timestamp": datetime.utcnow().isoformat(),
            "totals": self.get_totals().asdict(),
            "breakdown": self.get_cost_breakdown(),
            "units": [asdict(m) for m in self._metrics],
            "budget_config": asdict(self.budget_config)
        }
        
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)
        
        return str(output_path)


@dataclass
class CostTotals:
    """Total costs and tokens"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    unit_count: int = 0
    
    def asdict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "unit_count": self.unit_count
        }


class BudgetExceededException(Exception):
    """Exception raised when budget is exceeded"""
    
    def __init__(self, message: str, action: str):
        super().__init__(message)
        self.action = action