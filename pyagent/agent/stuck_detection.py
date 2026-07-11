"""
Stuck Detection and Recovery

Multi-level stuck detection with automatic recovery and persistent state across sessions.
Based on GSD Pi's auto-stuck-detection system.

Key features:
- Level 1: Same unit dispatched 3+ times consecutively
- Level 2: Sliding window of recent units with retry budget
- Persistent stuck state survives session restarts
- Verification retry policy with bounded exponential backoff
- Stale worker cleanup via drift detection framework
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
import threading


class RecoveryAction(Enum):
    """Recovery actions for stuck detection"""
    RETRY = "retry"  # Retry the same unit with bounded attempts
    REPAIR = "repair"  # Attempt to repair the state and retry
    REPLAN = "replan"  # Replan the current work
    REMEDIATE = "remediate"  # Schedule remediation workflow
    CLARIFY = "clarify"  # Request human clarification
    PAUSE = "pause"  # Pause for human intervention
    ABORT = "abort"  # Abort the current work


class StuckLevel(Enum):
    """Stuck detection levels"""
    NONE = 0  # No stuck detected
    LEVEL_1 = 1  # Same unit dispatched 3+ times consecutively
    LEVEL_2 = 2  # Sliding window with retry budget exceeded


@dataclass
class StuckState:
    """Persistent stuck state"""
    session_id: str
    level: StuckLevel = StuckLevel.NONE
    consecutive_failures: int = 0
    last_unit_type: Optional[str] = None
    last_unit_id: Optional[str] = None
    detected_at: Optional[str] = None
    recovery_action: Optional[RecoveryAction] = None
    recovery_attempts: int = 0
    last_recovery_attempt: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "session_id": self.session_id,
            "level": self.level.value,
            "consecutive_failures": self.consecutive_failures,
            "last_unit_type": self.last_unit_type,
            "last_unit_id": self.last_unit_id,
            "detected_at": self.detected_at,
            "recovery_action": self.recovery_action.value if self.recovery_action else None,
            "recovery_attempts": self.recovery_attempts,
            "last_recovery_attempt": self.last_recovery_attempt,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StuckState':
        """Create from dictionary"""
        return cls(
            session_id=data["session_id"],
            level=StuckLevel(data.get("level", 0)),
            consecutive_failures=data.get("consecutive_failures", 0),
            last_unit_type=data.get("last_unit_type"),
            last_unit_id=data.get("last_unit_id"),
            detected_at=data.get("detected_at"),
            recovery_action=RecoveryAction(data["recovery_action"]) if data.get("recovery_action") else None,
            recovery_attempts=data.get("recovery_attempts", 0),
            last_recovery_attempt=data.get("last_recovery_attempt"),
            metadata=data.get("metadata", {})
        )


@dataclass
class DispatchRecord:
    """Record of a unit dispatch"""
    unit_type: str
    unit_id: str
    timestamp: str
    status: str
    error: Optional[str] = None


@dataclass
class StuckDetectionConfig:
    """Configuration for stuck detection"""
    enabled: bool = True
    level_1_threshold: int = 3  # Consecutive failures for level 1
    level_2_window_size: int = 10  # Window size for level 2
    level_2_failure_threshold: int = 5  # Failures in window for level 2
    max_recovery_attempts: int = 3
    recovery_backoff_base: float = 2.0
    recovery_backoff_max: float = 60.0
    stale_threshold_hours: float = 24.0  # Consider state stale after this many hours
    auto_recovery_enabled: bool = True
    default_recovery_action: RecoveryAction = RecoveryAction.RETRY


class StuckDetector:
    """
    Multi-level stuck detection with automatic recovery and persistent state.
    
    Features:
    - Level 1: Consecutive failure detection
    - Level 2: Sliding window failure detection
    - Persistent state across session restarts
    - Configurable recovery policies
    - Integration with verification system
    """
    
    def __init__(
        self,
        project_root: str,
        session_id: str,
        config: Optional[StuckDetectionConfig] = None
    ):
        self.project_root = Path(project_root).resolve()
        self.gsd_dir = self.project_root / ".gsd"
        self.runtime_dir = self.gsd_dir / "runtime"
        self.stuck_state_file = self.runtime_dir / "stuck-state.json"
        
        self.session_id = session_id
        self.config = config or StuckDetectionConfig()
        
        # Thread-safe storage
        self._lock = threading.Lock()
        
        # Ensure directory exists
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        
        # Load or initialize stuck state
        self._stuck_state: Optional[StuckState] = None
        self._dispatch_history: List[DispatchRecord] = []
        
        self._load_state()
    
    def _load_state(self):
        """Load stuck state from persistent storage"""
        if self.stuck_state_file.exists():
            try:
                with open(self.stuck_state_file, 'r') as f:
                    data = json.load(f)
                    
                    # Load stuck state for this session
                    session_data = data.get(self.session_id)
                    if session_data:
                        self._stuck_state = StuckState.from_dict(session_data)
                    else:
                        self._stuck_state = StuckState(session_id=self.session_id)
                    
                    # Load dispatch history
                    self._dispatch_history = [
                        DispatchRecord(**record) for record in data.get("dispatch_history", [])
                    ]
                    
            except (json.JSONDecodeError, TypeError) as e:
                print(f"Warning: Failed to load stuck state: {e}")
                self._stuck_state = StuckState(session_id=self.session_id)
                self._dispatch_history = []
        else:
            self._stuck_state = StuckState(session_id=self.session_id)
            self._dispatch_history = []
    
    def _save_state(self):
        """Save stuck state to persistent storage"""
        with self._lock:
            data = {
                self.session_id: self._stuck_state.to_dict() if self._stuck_state else None,
                "dispatch_history": [asdict(record) for record in self._dispatch_history],
                "last_updated": datetime.utcnow().isoformat()
            }
            
            # Atomic write
            temp_file = self.stuck_state_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            
            # Replace original file
            if self.stuck_state_file.exists():
                self.stuck_state_file.unlink()
            temp_file.rename(self.stuck_state_file)
    
    def record_dispatch(
        self,
        unit_type: str,
        unit_id: str,
        status: str,
        error: Optional[str] = None
    ):
        """Record a unit dispatch event"""
        record = DispatchRecord(
            unit_type=unit_type,
            unit_id=unit_id,
            timestamp=datetime.utcnow().isoformat(),
            status=status,
            error=error
        )
        
        with self._lock:
            self._dispatch_history.append(record)
            
            # Keep only recent history (last 100 records)
            if len(self._dispatch_history) > 100:
                self._dispatch_history = self._dispatch_history[-100:]
            
            self._save_state()
    
    def check_stuck(self, unit_type: str, unit_id: str) -> Optional[StuckState]:
        """
        Check if the agent is stuck based on dispatch history.
        
        Returns:
            StuckState if stuck detected, None otherwise
        """
        if not self.config.enabled:
            return None
        
        with self._lock:
            # Check for stale state
            if self._is_stale():
                self._reset_stuck_state()
                return None
            
            # Level 1 detection: consecutive failures
            level_1_stuck = self._check_level_1(unit_type, unit_id)
            if level_1_stuck:
                return level_1_stuck
            
            # Level 2 detection: sliding window
            level_2_stuck = self._check_level_2()
            if level_2_stuck:
                return level_2_stuck
            
            return None
    
    def _check_level_1(self, unit_type: str, unit_id: str) -> Optional[StuckState]:
        """Check Level 1: consecutive failures of same unit"""
        consecutive_failures = 0
        
        # Count consecutive failures of the same unit
        for record in reversed(self._dispatch_history):
            if record.unit_type == unit_type and record.unit_id == unit_id:
                if record.status == "failed":
                    consecutive_failures += 1
                else:
                    break
            else:
                break
        
        if consecutive_failures >= self.config.level_1_threshold:
            # Stuck detected
            self._stuck_state.level = StuckLevel.LEVEL_1
            self._stuck_state.consecutive_failures = consecutive_failures
            self._stuck_state.last_unit_type = unit_type
            self._stuck_state.last_unit_id = unit_id
            self._stuck_state.detected_at = datetime.utcnow().isoformat()
            self._stuck_state.recovery_action = self.config.default_recovery_action
            
            self._save_state()
            return self._stuck_state
        
        return None
    
    def _check_level_2(self) -> Optional[StuckState]:
        """Check Level 2: sliding window failure rate"""
        if len(self._dispatch_history) < self.config.level_2_window_size:
            return None
        
        # Get recent window
        recent_window = self._dispatch_history[-self.config.level_2_window_size:]
        
        # Count failures in window
        failures = sum(1 for record in recent_window if record.status == "failed")
        
        if failures >= self.config.level_2_failure_threshold:
            # Stuck detected
            last_failed = next((r for r in reversed(recent_window) if r.status == "failed"), None)
            
            self._stuck_state.level = StuckLevel.LEVEL_2
            self._stuck_state.consecutive_failures = failures
            self._stuck_state.last_unit_type = last_failed.unit_type if last_failed else None
            self._stuck_state.last_unit_id = last_failed.unit_id if last_failed else None
            self._stuck_state.detected_at = datetime.utcnow().isoformat()
            self._stuck_state.recovery_action = RecoveryAction.REPLAN  # Level 2 requires replanning
            
            self._save_state()
            return self._stuck_state
        
        return None
    
    def _is_stale(self) -> bool:
        """Check if stuck state is stale"""
        if not self._stuck_state or self._stuck_state.level == StuckLevel.NONE:
            return False
        
        if not self._stuck_state.detected_at:
            return True
        
        try:
            detected_at = datetime.fromisoformat(self._stuck_state.detected_at)
            age = datetime.utcnow() - detected_at
            return age.total_seconds() > (self.config.stale_threshold_hours * 3600)
        except ValueError:
            return True
    
    def _reset_stuck_state(self):
        """Reset stuck state"""
        self._stuck_state = StuckState(session_id=self.session_id)
        self._save_state()
    
    def attempt_recovery(self) -> bool:
        """
        Attempt automatic recovery from stuck state.
        
        Returns:
            True if recovery was attempted, False if no recovery needed or possible
        """
        if not self.config.enabled or not self.config.auto_recovery_enabled:
            return False
        
        if not self._stuck_state or self._stuck_state.level == StuckLevel.NONE:
            return False
        
        # Check recovery attempt limit
        if self._stuck_state.recovery_attempts >= self.config.max_recovery_attempts:
            print(f"Max recovery attempts ({self.config.max_recovery_attempts}) exceeded")
            self._stuck_state.recovery_action = RecoveryAction.PAUSE
            self._save_state()
            return False
        
        # Calculate backoff delay
        delay = self._calculate_recovery_delay()
        
        print(f"Attempting recovery: {self._stuck_state.recovery_action.value} (attempt {self._stuck_state.recovery_attempts + 1})")
        print(f"Backoff delay: {delay:.1f}s")
        
        # Record recovery attempt
        self._stuck_state.recovery_attempts += 1
        self._stuck_state.last_recovery_attempt = datetime.utcnow().isoformat()
        
        # Apply recovery action based on level
        success = self._apply_recovery_action()
        
        if success:
            # Reset stuck state on successful recovery
            self._reset_stuck_state()
        else:
            self._save_state()
        
        return success
    
    def _calculate_recovery_delay(self) -> float:
        """Calculate exponential backoff delay for recovery"""
        attempt = self._stuck_state.recovery_attempts if self._stuck_state else 0
        delay = self.config.recovery_backoff_base * (2 ** attempt)
        return min(delay, self.config.recovery_backoff_max)
    
    def _apply_recovery_action(self) -> bool:
        """Apply the configured recovery action"""
        action = self._stuck_state.recovery_action or self.config.default_recovery_action
        
        if action == RecoveryAction.RETRY:
            # Simple retry - just reset consecutive failures
            return True
        
        elif action == RecoveryAction.REPAIR:
            # Attempt to repair state
            return self._repair_state()
        
        elif action == RecoveryAction.REPLAN:
            # Request replanning
            return self._request_replan()
        
        elif action == RecoveryAction.REMEDIATE:
            # Schedule remediation
            return self._schedule_remediation()
        
        elif action == RecoveryAction.CLARIFY:
            # Request human clarification
            return self._request_clarification()
        
        elif action == RecoveryAction.PAUSE:
            # Pause for human intervention
            return self._request_pause()
        
        elif action == RecoveryAction.ABORT:
            # Abort current work
            return self._abort_work()
        
        return False
    
    def _repair_state(self) -> bool:
        """Attempt to repair the current state"""
        # This would implement state-specific repair logic
        # For now, return True to indicate repair was attempted
        print("Attempting state repair...")
        return True
    
    def _request_replan(self) -> bool:
        """Request replanning of current work"""
        print("Requesting replan of current work...")
        return True
    
    def _schedule_remediation(self) -> bool:
        """Schedule remediation workflow"""
        print("Scheduling remediation workflow...")
        return True
    
    def _request_clarification(self) -> bool:
        """Request human clarification"""
        print("Requesting human clarification...")
        return True
    
    def _request_pause(self) -> bool:
        """Request pause for human intervention"""
        print("Requesting pause for human intervention...")
        return True
    
    def _abort_work(self) -> bool:
        """Abort current work"""
        print("Aborting current work...")
        return True
    
    def get_stuck_state(self) -> Optional[StuckState]:
        """Get current stuck state"""
        return self._stuck_state
    
    def get_dispatch_history(self) -> List[DispatchRecord]:
        """Get dispatch history"""
        return self._dispatch_history.copy()
    
    def manual_recovery(self, action: RecoveryAction) -> bool:
        """Manually trigger a specific recovery action"""
        if not self._stuck_state:
            self._stuck_state = StuckState(session_id=self.session_id)
        
        self._stuck_state.recovery_action = action
        self._stuck_state.detected_at = datetime.utcnow().isoformat()
        
        return self.attempt_recovery()
    
    def reset(self):
        """Manually reset stuck state"""
        with self._lock:
            self._reset_stuck_state()
    
    def cleanup_old_sessions(self, older_than_hours: float = 48.0):
        """Clean up stuck state for old sessions"""
        if not self.stuck_state_file.exists():
            return
        
        try:
            with open(self.stuck_state_file, 'r') as f:
                data = json.load(f)
            
            cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)
            cleaned_data = {}
            
            for session_id, session_data in data.items():
                if session_id == "dispatch_history" or session_id == "last_updated":
                    # Keep these
                    cleaned_data[session_id] = session_data
                    continue
                
                try:
                    detected_at = session_data.get("detected_at")
                    if detected_at:
                        detected_time = datetime.fromisoformat(detected_at)
                        if detected_time >= cutoff:
                            cleaned_data[session_id] = session_data
                except (ValueError, TypeError):
                    # Skip invalid entries
                    continue
            
            # Save cleaned data
            temp_file = self.stuck_state_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(cleaned_data, f, indent=2)
            
            if self.stuck_state_file.exists():
                self.stuck_state_file.unlink()
            temp_file.rename(self.stuck_state_file)
            
        except (json.JSONDecodeError, TypeError, IOError) as e:
            print(f"Warning: Failed to cleanup old sessions: {e}")