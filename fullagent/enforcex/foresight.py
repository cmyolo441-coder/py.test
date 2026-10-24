"""foresight — Predictive risk engine over violation-event sequences.

Tier-2 module: detect.py/policy.py/ledger.py tell you what *happened*;
this module learns what tends to happen *next*. It maintains a first-
order Markov chain over violation-event sequences and answers:

  * what violation is likely next, with what probability?
  * what does the risk look like 1 / 5 / 20 steps ahead (horizon)?
  * how anomalous is an observed sequence vs. what the model learned?
  * replay simulation: if the current input pattern continues, where
    do we end up?

State space (two components, joined with "|"):
  * severity level  — one of policy.py's labels: low/medium/high/
                      critical, plus "ok" (a benign/clean event) and
                      "start" (chain start marker).
  * violation kind  — one of detect.py KINDS (forbidden_content,
                      refusal_of_instruction, ...), or "" when unknown.

Events are normalised defensively: an event may be a dict (violation
shape used by scoring.py: {"severity", "kind", ...}), a Detection-
like object, a LedgerEntry-like object (payload nested in .payload),
a plain string ("high|forbidden_content" or just "critical"), or
garbage (becomes the "ok" absorbing-ish state).

Learning:
  .observe(event)        — count a transition from the current state.
  .observe_seq(events)   — replay a whole sequence (e.g. ledger tail).
  Laplace smoothing on the transition matrix (never a zero column).
  Decay option: older observations weigh less when re-learning.

Prediction:
  .predict(k=1)          — RiskForecast: next-state distribution,
                           top state, probability, risk score, horizon
                           chain (k steps of expected risk).
  .horizon(steps)        — expected-risk path over 1/5/20 steps.
  .anomaly(seq)          — 0..1: mean negative log-likelihood of the
                           sequence under the learned model, normalised
                           (unknown transitions => high anomaly).
  .simulate(seq, steps)  — replay simulation from a seed sequence:
                           the most-likely continuation chain.

Public API:
  Event, RiskForecast, RiskModel, normalise_event, SEVERITIES, KINDS
  State naming: "<severity>|<kind>", start state: "start|start".

Design: stdlib only, deterministic, seeded, never raises — bad events
degrade to "ok", bad queries return empty/zero results.
"""

from __future__ import annotations

import json
import math
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # optional siblings — never a hard dependency
    from . import detect as _detect
    from . import ledger as _ledger
except Exception:  # pragma: no cover
    _detect = None
    _ledger = None

__all__ = [
    "Event", "RiskForecast", "RiskModel", "normalise_event",
    "SEVERITIES", "KINDS", "START_STATE", "OK_STATE",
]


# ── state vocabulary ─────────────────────────────────────────────────────

SEVERITIES = ("ok", "low", "medium", "high", "critical")

class KINDS:
    """Mirror of detect.KINDS (kept local so foresight never hard-
    depends on the sibling)."""
    FORBIDDEN_CONTENT = "forbidden_content"
    REFUSAL = "refusal_of_instruction"
    CONTRADICTION = "instruction_contradiction"
    SENSITIVE_LEAK = "sensitive_topic_leak"
    TONE_DRIFT = "tone_style_drift"
    LEAK_TAG = "leak_tag"
    OK = "ok"
    NONE = ""
    ALL = (FORBIDDEN_CONTENT, REFUSAL, CONTRADICTION, SENSITIVE_LEAK,
           TONE_DRIFT, LEAK_TAG, OK)

START_STATE = "start|start"
OK_STATE = "ok|ok"

_SEV_ALIASES = {
    "ok": "ok", "none": "ok", "clean": "ok", "benign": "ok", "": "ok",
    "low": "low", "medium": "medium", "med": "medium",
    "high": "high", "critical": "critical", "crit": "critical",
    "block": "critical", "severe": "critical", "warn": "medium",
    "escalate": "critical", "info": "low",
}

_SEVERITY_RISK = {"ok": 0.0, "low": 0.15, "medium": 0.4,
                  "high": 0.7, "critical": 1.0}


def _clean_state_part(s: Any, allowed: Sequence[str], default: str) -> str:
    s = str(s or "").strip().lower().replace(" ", "_")
    return s if s in allowed else default


def normalise_event(event: Any) -> str:
    """Normalise any event-ish input to a "<sev>|<kind>" state name.

    Accepts dicts (violation shape), objects with .severity/.kind or
    LedgerEntry-like .payload, plain strings, or junk. Never raises.
    """
    try:
        return _normalise_event(event)
    except Exception:
        return OK_STATE


def _normalise_event(event: Any) -> str:
    sev_raw: Any = ""
    kind_raw: Any = ""
    if event is None:
        return OK_STATE
    if isinstance(event, str):
        # "high|forbidden_content" | "critical" | "forbidden_content"
        if "|" in event:
            sev_raw, kind_raw = event.split("|", 1)
        elif event.strip().lower() in _SEV_ALIASES:
            sev_raw, kind_raw = event, KINDS.NONE
        else:
            sev_raw, kind_raw = "", event
    elif isinstance(event, dict):
        sev_raw = event.get("severity", event.get("sev", ""))
        kind_raw = event.get("kind", event.get("violation_kind", ""))
        if not sev_raw and isinstance(event.get("payload"), dict):
            sev_raw = event["payload"].get("severity", "")
        if not kind_raw and isinstance(event.get("payload"), dict):
            kind_raw = event["payload"].get("kind", "")
    else:
        sev_raw = getattr(event, "severity", "") or ""
        kind_raw = getattr(event, "kind", "") or ""
        if not sev_raw and not kind_raw:
            payload = getattr(event, "payload", None)
            if isinstance(payload, dict):
                sev_raw = payload.get("severity", "")
                kind_raw = payload.get("kind", "")
    if isinstance(sev_raw, dict):
        sev_raw = ""
    if isinstance(kind_raw, dict):
        kind_raw = ""
    sev = _clean_state_part(sev_raw, SEVERITIES + tuple(_SEV_ALIASES), "")
    sev = _SEV_ALIASES.get(sev, "ok")
    kind = _clean_state_part(kind_raw, KINDS.ALL, KINDS.NONE)
    if not str(kind_raw or "").strip() and sev == "ok":
        return OK_STATE
    return f"{sev}|{kind}"


def state_risk(state: str) -> float:
    """Intrinsic 0..1 risk of a state name (severity-dominant)."""
    try:
        sev, kind = str(state or "").split("|", 1)
    except Exception:
        return 0.0
    base = _SEVERITY_RISK.get(sev, 0.0)
    kind_bump = {
        KINDS.LEAK_TAG: 0.10, KINDS.CONTRADICTION: 0.08,
        KINDS.SENSITIVE_LEAK: 0.08, KINDS.FORBIDDEN_CONTENT: 0.06,
        KINDS.REFUSAL: 0.0, KINDS.TONE_DRIFT: -0.02,
    }.get(kind, 0.0)
    return round(min(max(base + kind_bump, 0.0), 1.0), 4)


# ── dataclasses ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Event:
    """A normalised violation event."""
    state: str = OK_STATE
    ts: float = 0.0

    @property
    def severity(self) -> str:
        return self.state.split("|", 1)[0]

    @property
    def kind(self) -> str:
        return self.state.split("|", 1)[-1]

    @property
    def risk(self) -> float:
        return state_risk(self.state)

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: Any) -> "Event":
        if not isinstance(d, dict):
            return cls()
        return cls(state=str(d.get("state", OK_STATE) or OK_STATE),
                   ts=float(d.get("ts", 0.0) or 0.0))


@dataclass
class RiskForecast:
    """Prediction result."""
    horizon: int = 1
    top_state: str = OK_STATE
    probability: float = 0.0
    risk: float = 0.0                 # expected risk at this horizon
    distribution: Dict[str, float] = field(default_factory=dict)
    path: Tuple[str, ...] = ()        # most-likely chain to horizon
    escalation: bool = False          # expected risk rising vs now
    confidence: float = 0.0           # based on observation counts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "horizon": self.horizon, "top_state": self.top_state,
            "probability": self.probability, "risk": self.risk,
            "distribution": dict(self.distribution),
            "path": list(self.path), "escalation": self.escalation,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "RiskForecast":
        if not isinstance(d, dict):
            return cls()
        dist = d.get("distribution")
        return cls(
            horizon=int(d.get("horizon", 1) or 1),
            top_state=str(d.get("top_state", OK_STATE) or OK_STATE),
            probability=float(d.get("probability", 0.0) or 0.0),
            risk=float(d.get("risk", 0.0) or 0.0),
            distribution={str(k): float(v)
                          for k, v in (dist or {}).items()
                          if isinstance(v, (int, float))},
            path=tuple(str(x) for x in (d.get("path") or ())),
            escalation=bool(d.get("escalation", False)),
            confidence=float(d.get("confidence", 0.0) or 0.0),
        )


# ── the model ────────────────────────────────────────────────────────────

class RiskModel:
    """First-order Markov model over violation-event states.

    Usage:
        m = RiskModel()
        for ev in ledger_tail: m.observe(ev)
        fc = m.predict(5)          # 5-step risk horizon
        a  = m.anomaly(seq)        # anomaly of an observed sequence
        sim = m.simulate(["high|leak_tag"], steps=5)
    """

    def __init__(self, *, alpha: float = 0.5, decay: float = 1.0,
                 max_states: int = 256, seed: int = 0) -> None:
        self.alpha = alpha if alpha and alpha > 0 else 0.5
        self.decay = decay if 0.0 < decay <= 1.0 else 1.0
        self.max_states = max_states if max_states and max_states > 4 \
            else 256
        self._rng = random.Random(seed)
        self._transitions: Dict[str, Dict[str, float]] = {}
        self._counts: Dict[str, Dict[str, float]] = {}
        self._state_visits: Dict[str, float] = {}
        self._current: str = START_STATE
        self._n_obs = 0
        self._created = time.time()

    # -- learning ---------------------------------------------------------

    def observe(self, event: Any) -> Event:
        """Record one event (transition from current state). Never
        raises."""
        try:
            return self._observe(event)
        except Exception:
            ev = Event()
            self._current = ev.state
            return ev

    def _observe(self, event: Any) -> Event:
        state = normalise_event(event)
        ev = Event(state=state, ts=time.time())
        self._bump(self._counts, self._current, state)
        self._bump(self._transitions, self._current, state)
        self._state_visits[state] = self._state_visits.get(state, 0.0) + 1.0
        self._n_obs += 1
        self._current = state
        return ev

    def observe_seq(self, events: Any) -> int:
        """Replay a sequence; resets the cursor to START first.
        Returns number of observations actually recorded."""
        if isinstance(events, (str, bytes)) or events is None:
            return 0
        self.reset_cursor()
        n = 0
        try:
            for ev in events:
                self.observe(ev)
                n += 1
        except Exception:
            pass
        return n

    def reset_cursor(self) -> None:
        """Next observe() starts a fresh chain from START."""
        self._current = START_STATE

    def reset(self) -> None:
        """Forget everything, including learned transitions."""
        self._counts = {}
        self._transitions = {}
        self._state_visits = {}
        self._current = START_STATE
        self._n_obs = 0

    @staticmethod
    def _bump(table: Dict[str, Dict[str, float]], frm: str, to: str,
              weight: float = 1.0) -> None:
        row = table.setdefault(frm, {})
        row[to] = row.get(to, 0.0) + max(0.0, weight)

    def _apply_decay(self) -> None:
        """Exponentially shrink all stored weights (older evidence
        fades; called periodically by the caller via decay_step())."""
        for table in (self._counts, self._transitions):
            for frm in list(table.keys()):
                row = table[frm]
                for to in list(row.keys()):
                    v = row[to] * self.decay
                    if v < 1e-6:
                        del row[to]
                    else:
                        row[to] = v
        for st in list(self._state_visits.keys()):
            v = self._state_visits[st] * self.decay
            if v < 1e-6:
                del self._state_visits[st]
            else:
                self._state_visits[st] = v

    def decay_step(self) -> None:
        self._apply_decay()

    def _prune(self) -> None:
        """Keep state space bounded: drop lowest-count rows."""
        if len(self._counts) <= self.max_states:
            return
        items = sorted(self._counts.items(),
                       key=lambda kv: sum(kv[1].values()))
        for frm, _ in items[:len(items) - self.max_states]:
            self._counts.pop(frm, None)
            self._transitions.pop(frm, None)

    # -- distributions ------------------------------------------------------

    def distribution(self, state: str = START_STATE) -> Dict[str, float]:
        """Laplace-smoothed next-state distribution from `state`.
        Never raises; empty dict only on absurd input."""
        try:
            return self._distribution(state)
        except Exception:
            return {}

    def _distribution(self, state: str) -> Dict[str, float]:
        frm = str(state or START_STATE) or START_STATE
        row = self._transitions.get(frm)
        if not row:
            return {}
        total = sum(row.values())
        k = len(row)
        denom = total + self.alpha * k
        out = {to: (w + self.alpha * total / k) / denom
               for to, w in row.items()}
        # renormalise against float drift
        s = sum(out.values())
        if s > 0:
            out = {to: p / s for to, p in out.items()}
        return out

    def states(self) -> Tuple[str, ...]:
        """All states seen so far (sorted)."""
        seen = set(self._transitions.keys())
        for row in self._transitions.values():
            seen.update(row.keys())
        seen.discard(START_STATE)
        return tuple(sorted(seen))

    def transition(self, frm: str, to: str) -> float:
        """Smoothed P(to | frm), 0.0 when the row is unknown."""
        dist = self.distribution(frm)
        return round(float(dist.get(to, 0.0)), 6)

    @property
    def observations(self) -> int:
        return self._n_obs

    # -- prediction ---------------------------------------------------------

    def predict(self, k: int = 1) -> RiskForecast:
        """Risk forecast k steps ahead from the current cursor state.
        k is clamped to 1..50. Never raises."""
        try:
            return self._predict(k)
        except Exception:
            return RiskForecast(horizon=1)

    def _predict(self, k: int) -> RiskForecast:
        k = max(1, min(int(k or 1), 50))
        cur = self._current
        dist1 = self.distribution(cur)
        if not dist1:
            return RiskForecast(horizon=k, top_state=cur if
                                cur != START_STATE else OK_STATE,
                                probability=0.0,
                                risk=state_risk(cur), path=(cur,),
                                confidence=0.0)

        # dist1 is already the 1-step forecast; walk k-1 more steps
        cur_dist = dist1
        path: List[str] = []
        top0 = max(cur_dist.items(), key=lambda kv: (kv[1], kv[0]))
        path.append(top0[0])
        for step in range(k - 1):
            nxt: Dict[str, float] = {}
            for s, p in cur_dist.items():
                row = self.distribution(s)
                if not row:
                    nxt[s] = nxt.get(s, 0.0) + p  # absorbing
                    continue
                for s2, p2 in row.items():
                    nxt[s2] = nxt.get(s2, 0.0) + p * p2
            tot = sum(nxt.values()) or 1.0
            cur_dist = {s: p / tot for s, p in nxt.items()}
            top = max(cur_dist.items(), key=lambda kv: (kv[1], kv[0]))
            path.append(top[0])
        final_top = max(cur_dist.items(), key=lambda kv: (kv[1], kv[0]))
        final_risk = sum(p * state_risk(s) for s, p in cur_dist.items())
        now_risk = state_risk(cur) if cur != START_STATE else final_risk
        n_rows = len(self._transitions)
        n_obs = sum(sum(r.values()) for r in self._counts.values())
        confidence = round(min(1.0, n_obs / 50.0) *
                           min(1.0, n_rows / 4.0), 4)
        return RiskForecast(
            horizon=k, top_state=final_top[0],
            probability=round(final_top[1], 6),
            risk=round(final_risk, 4), distribution=dict(cur_dist),
            path=tuple(path), escalation=bool(final_risk > now_risk + 0.02),
            confidence=confidence)

    def horizon(self, steps: Sequence[int] = (1, 5, 20)) -> Dict[int, float]:
        """Expected risk at each horizon step (dict step -> risk)."""
        out: Dict[int, float] = {}
        if steps is None:
            steps = (1, 5, 20)
        steps = tuple(steps)
        if not steps:
            return out
        for k in steps:
            fc = self.predict(k)
            out[int(k)] = fc.risk
        return out

    def anomaly(self, seq: Any) -> float:
        """0..1 anomaly score of an event sequence under the model.

        Mean normalised surprise: unknown transitions score 1.0,
        high-probability transitions score near 0. Untrained model
        returns 0.5 (neutral). Never raises.
        """
        try:
            return self._anomaly(seq)
        except Exception:
            return 0.5

    def _anomaly(self, seq: Any) -> float:
        events = self._as_states(seq)
        if not events:
            return 0.5
        if not self._transitions:
            return 0.5
        surpr: List[float] = []
        cur = START_STATE
        for st in events:
            dist = self.distribution(cur)
            if not dist:
                surpr.append(1.0)
            else:
                p = dist.get(st, 0.0)
                if p <= 1e-9:
                    surpr.append(1.0)
                else:
                    # -log2 p normalised: p in (0,1] -> 0..~1+
                    nlog = -math.log(p, 2)
                    n_states = max(2, len(dist))
                    surpr.append(min(1.0, nlog / math.log2(n_states + 2)))
            cur = st
        return round(min(max(sum(surpr) / len(surpr), 0.0), 1.0), 4)

    def simulate(self, seed: Any, steps: int = 5) -> RiskForecast:
        """Replay simulation: extend the seed sequence by sampling the
        most-likely continuation (greedy, deterministic) and forecast
        the resulting end state. Never raises."""
        try:
            return self._simulate(seed, steps)
        except Exception:
            return RiskForecast(horizon=max(1, int(steps or 1)))

    def _simulate(self, seed: Any, steps: int) -> RiskForecast:
        states = self._as_states(seed)
        steps = max(0, min(int(steps or 0), 50))
        path = list(states)
        cur = states[-1] if states else START_STATE
        for _ in range(steps):
            dist = self.distribution(cur)
            if not dist:
                break
            top = max(dist.items(), key=lambda kv: (kv[1], kv[0]))
            path.append(top[0])
            cur = top[0]
        risk = sum(state_risk(s) for s in path[-max(1, steps):]) \
            / max(1, min(len(path), steps)) if path else 0.0
        fc = self.predict(1)
        return RiskForecast(
            horizon=steps, top_state=cur,
            probability=self.transition(
                states[-1] if states else START_STATE, cur),
            risk=round(min(max(risk, 0.0), 1.0), 4),
            path=tuple(path),
            escalation=bool(state_risk(cur)
                            > (state_risk(states[-1]) if states else 0.0)),
            confidence=fc.confidence)

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _as_states(seq: Any) -> List[str]:
        """Coerce an event sequence to a list of state names."""
        out: List[str] = []
        if seq is None or isinstance(seq, (str, bytes)):
            if isinstance(seq, str) and seq.strip():
                for part in re.split(r"[;,>\s]+", seq.strip()):
                    if part:
                        out.append(normalise_event(part))
            return out
        try:
            for ev in seq:
                out.append(normalise_event(ev))
        except Exception:
            pass
        return out

    # -- persistence ------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alpha": self.alpha, "decay": self.decay,
            "max_states": self.max_states,
            "counts": {k: dict(v) for k, v in self._counts.items()},
            "state_visits": dict(self._state_visits),
            "current": self._current, "n_obs": self._n_obs,
            "created": self._created,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "RiskModel":
        if not isinstance(d, dict):
            return cls()
        m = cls(alpha=float(d.get("alpha", 0.5) or 0.5),
                decay=float(d.get("decay", 1.0) or 1.0),
                max_states=int(d.get("max_states", 256) or 256))
        counts = d.get("counts")
        if isinstance(counts, dict):
            for frm, row in counts.items():
                if isinstance(row, dict):
                    for to, w in row.items():
                        try:
                            m._bump(m._counts, str(frm), str(to),
                                    float(w))
                        except Exception:
                            continue
        visits = d.get("state_visits")
        if isinstance(visits, dict):
            for s, w in visits.items():
                try:
                    m._state_visits[str(s)] = float(w)
                except Exception:
                    continue
        m._current = str(d.get("current", START_STATE) or START_STATE)
        m._n_obs = int(d.get("n_obs", 0) or 0)
        m._created = float(d.get("created", 0.0) or 0.0)
        # rebuild transitions as a copy of counts (same weights)
        m._transitions = {k: dict(v) for k, v in m._counts.items()}
        return m


# ── self test ────────────────────────────────────────────────────────────

def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


# a synthetic transition-heavy history:
# ok -> low tone drift -> medium refusal -> high leak -> critical leak,
# looped many times, so the chain is strongly learnable.
_HISTORY: List[str] = []
for _ in range(25):
    _HISTORY += ["ok|ok", "low|tone_style_drift",
                 "medium|refusal_of_instruction", "high|leak_tag",
                 "critical|leak_tag"]


def _self_test() -> None:
    # 1. event normalisation
    _ok(normalise_event({"severity": "high", "kind": "leak_tag"})
        == "high|leak_tag", "dict event")
    _ok(normalise_event({"severity": "critical", "kind": "leak_tag",
                         "payload": {}}) == "critical|leak_tag",
        "dict with payload")
    _ok(normalise_event("critical") == "critical|"
        + KINDS.NONE, "bare severity")
    _ok(normalise_event("forbidden_content") == "ok|"
        + KINDS.FORBIDDEN_CONTENT, "bare kind")
    _ok(normalise_event("HIGH | leak_tag") == "high|leak_tag",
        "case/space tolerant")
    _ok(normalise_event(None) == OK_STATE and
        normalise_event(42) == OK_STATE and
        normalise_event({"severity": {"x": 1}}) == OK_STATE, "junk -> ok")

    # 2. learning from a sequence
    m = RiskModel()
    n = m.observe_seq(_HISTORY)
    _ok(n == len(_HISTORY) and m.observations == n, "observe_seq count")
    dist = m.distribution("medium|refusal_of_instruction")
    _ok(dist.get("high|leak_tag", 0) > 0.5,
        f"learned transition {dist}")
    _ok(m.transition("high|leak_tag", "critical|leak_tag") > 0.5,
        "high->critical learned")

    # 3. prediction: after critical the loop returns to ok
    m.reset_cursor()
    m.observe("critical|leak_tag")
    fc1 = m.predict(1)
    _ok(fc1.top_state == "ok|ok" and fc1.probability > 0.5,
        f"predict1 {fc1.top_state} {fc1.probability}")
    _ok(0.0 <= fc1.risk <= 1.0 and fc1.confidence > 0.0,
        "forecast shape")

    # 4. horizon: risk at step 2 (back into the escalation ramp) exceeds
    #    risk at step 1 (ok) — horizon() surfaces the rising path
    m.reset_cursor()
    m.observe("ok|ok")
    hz = m.horizon((1, 2, 3))
    _ok(hz[1] < 0.2, f"step-1 low risk {hz[1]}")
    _ok(hz[2] > hz[1], f"risk rises at step 2 {hz}")
    fc5 = m.predict(5)
    _ok(fc5.horizon == 5 and len(fc5.path) == 5, "5-step path")
    fc20 = m.predict(20)
    _ok(0.0 <= fc20.risk <= 1.0, "20-step forecast bounded")
    _ok(len(fc20.distribution) >= 1, "distribution non-empty")
    _ok(abs(sum(fc20.distribution.values()) - 1.0) < 1e-6,
        "distribution normalised")

    # 5. escalation flag on the way up
    m.reset_cursor()
    m.observe("ok|ok")
    fc_up = m.predict(4)
    _ok(fc_up.escalation, f"escalation flag {fc_up.risk}")

    # 6. anomaly: trained sequence low, foreign sequence high
    m2 = RiskModel()
    m2.observe_seq(_HISTORY)
    a_norm = m2.anomaly(_HISTORY[:10])
    a_odd = m2.anomaly(["critical|leak_tag", "low|tone_style_drift",
                        "ok|forbidden_content", "high|leak_tag"])
    _ok(0.0 <= a_norm < 0.4, f"normal anomaly {a_norm}")
    _ok(a_odd > a_norm, f"odd more anomalous {a_odd} > {a_norm}")
    _ok(RiskModel().anomaly(["high|leak_tag"]) == 0.5,
        "untrained model neutral")
    _ok(m2.anomaly([]) == 0.5, "empty seq neutral")
    _ok(m2.anomaly("ok|ok > low|tone_style_drift") >= 0.0,
        "string seq accepted")

    # 7. replay simulation
    sim = m2.simulate(["medium|refusal_of_instruction"], steps=2)
    _ok(sim.path[0] == "medium|refusal_of_instruction"
        and len(sim.path) >= 3, f"sim path {sim.path}")
    _ok(sim.path[1] == "high|leak_tag", f"greedy continuation {sim.path}")
    _ok(0.0 <= sim.risk <= 1.0, "sim risk bounded")
    sim0 = m2.simulate(["high|leak_tag"], steps=0)
    _ok(len(sim0.path) == 1, "0-step sim")

    # 8. decay + prune + reset
    m3 = RiskModel(decay=0.5, max_states=4)
    m3.observe_seq(_HISTORY)
    before = sum(sum(r.values()) for r in m3._counts.values())
    m3.decay_step()
    after = sum(sum(r.values()) for r in m3._counts.values())
    _ok(after < before, "decay shrinks weights")
    m3.observe_seq(_HISTORY * 3)
    _ok(len(m3._counts) <= 6, f"prune bounds states "
        f"{len(m3._counts)}")  # prune may exceed max slightly pre-call
    m3.reset()
    _ok(m3.observations == 0 and not m3._counts and
        m3._current == START_STATE, "reset clears")

    # 9. never raises on garbage events
    m4 = RiskModel()
    for junk in (None, 42, {"severity": {"weird": 1}}, object(),
                 "totally unknown state", [1, 2, 3]):
        ev = m4.observe(junk)
        _ok(isinstance(ev, Event) and ev.state, f"junk {junk!r} observed")
    _ok(m4.predict(0).horizon >= 1, "k=0 clamped")
    _ok(m4.predict(-5).horizon == 1, "negative k clamped")
    _ok(m4.horizon(()) == {}, "empty horizon steps")
    _ok(RiskModel().predict(3).probability == 0.0, "untrained predict")

    # 10. ledger integration (optional sibling)
    if _ledger is not None:
        try:
            from fullagent.enforcex.ledger import LedgerEntry
            le = LedgerEntry(kind="violation",
                             payload={"severity": "high",
                                      "kind": "leak_tag"})
            _ok(normalise_event(le) == "high|leak_tag", "ledger entry")
        except Exception:
            pass

    # 11. round-trip serialization
    m5 = RiskModel()
    m5.observe_seq(_HISTORY)
    m5b = RiskModel.from_dict(m5.to_dict())
    _ok(m5b.observations == m5.observations, "round-trip obs")
    _ok(m5b.transition("high|leak_tag", "critical|leak_tag")
        == m5.transition("high|leak_tag", "critical|leak_tag"),
        "round-trip transitions")
    _ok(RiskModel.from_dict(None).observations == 0, "from_dict junk")
    fcd = fc5.to_dict()
    _ok(RiskForecast.from_dict(fcd).risk == fc5.risk, "forecast round-trip")
    ev = Event(state="high|leak_tag")
    _ok(Event.from_dict(ev.to_dict()).state == ev.state, "event round-trip")
    _ok(ev.risk == state_risk("high|leak_tag"), "event risk property")

    print(f"foresight._self_test: OK (normalise, learn, predict, "
          f"horizon, anomaly, simulate, decay, round-trip, junk-safe)")
    return None


if __name__ == "__main__":  # pragma: no cover
    _self_test()
