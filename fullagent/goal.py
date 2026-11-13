"""GOAL MODE — the convergence (Part VI).

"A goal that cannot be failed is not a goal." Here the goal stops being
text and becomes a first-class, machine-checkable object in the event log:
clauses with mandatory predicates, weights, anti-clauses, invariant
clauses, a computed distance, velocity, gravity-driven focus, an amendment
protocol, and a closing condition no model may declare satisfied.

Hard rules implemented mechanically (rung 1, pure Python):
  * §37.2  A clause without a machine-checkable predicate is REJECTED at
           contract load time unless explicitly marked advisory.
  * §38.1  Every tool call is attributed to a clause (correlation_id).
           An action serving no open clause is an OrphanAction.
  * §39.1  distance = 1 - Σ weight_i · confidence_i · proven_i.
           proven is binary and set ONLY by a Judge predicate result.
  * §39.3  A run cannot close ACHIEVED if any clause's only evidence is
           model judgement (confidence < 0.85) without a human waiver.
  * §37.4  Anti-clauses are re-checked after EVERY write event; a violation
           emits clause.regressed and the run cannot close.
  * §42.1  There is no DONE state a model may write. ACHIEVED is computed
           by the kernel from proof events.

All state is derived by folding the EventLog:
  goal.set            — the full contract (frozen as an event)
  clause.proven       — {clause, confidence, evidence_seq, proof_type}
  clause.regressed    — {clause, reason, seq}
  clause.waived       — {clause, reason}  (human waiver, recorded)
  goal.amendment      — {kind, rationale, verdict: pending|accepted|rejected}
  goal.focus          — {from, to, reason, gravity}
  goal.distance       — {distance, velocity, seq}
  goal.closed         — {state: ACHIEVED|PARTIAL|STALLED|BLOCKED|ABANDONED}
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from ._foundation import get_logger

_log = get_logger("goal")
from dataclasses import dataclass, field

from .kernel import Event, EventLog, fold

# ---------------------------------------------------------------------------
# §37.3 clause kinds and §39.3 proof confidence
# ---------------------------------------------------------------------------

CLAUSE_KINDS = ("OUTCOME", "ARTIFACT", "BEHAVIOUR", "CONSTRAINT",
                "QUALITY", "KNOWLEDGE")

# Proof type -> confidence (§39.3, honest grading). A clause's contribution
# to distance is weight * confidence, and only once proven binary.
PROOF_CONFIDENCE = {
    "suite_green": 1.00,              # full suite, snapshotted environment
    "human_approval": 1.00,           # the human is the highest authority
    "exit_code": 0.95,                # single command/test green
    "command_output_contains": 0.95,
    "tool_delta": 0.90,               # type-check / lint delta clean
    "ast_assert": 0.85,               # structure proven, behaviour not
    "file_matches": 0.85,
    "diff_assert": 0.85,
    "file_unchanged": 0.85,
    "file_contains": 0.80,
    "file_exists": 0.70,              # existence is not correctness
    "model_judgement": 0.50,          # capped; can never alone close a run
}

CLOSING_RULES = ("ALL", "WEIGHTED_THRESHOLD", "ORDERED")

# Terminal states (§42.1)
ACHIEVED, PARTIAL, STALLED, BLOCKED, ABANDONED = (
    "ACHIEVED", "PARTIAL", "STALLED", "BLOCKED", "ABANDONED")

# Every state a goal.closed event can carry. Once the kernel seals one of
# these, the contract is settled and stops demanding attribution (§38.1);
# without this the orphan gate dead-ends the agent forever after a goal
# completes (e.g. a fully PROVEN contract still blocks every shell command).
TERMINAL_STATES = frozenset(
    (ACHIEVED, PARTIAL, STALLED, BLOCKED, ABANDONED))

MIN_PROOF_CONFIDENCE = 0.85  # §39.3 hard rule for closing ACHIEVED

# §38.1 attribution gate exemptions. Session management (switching away
# from / out of a contract), planning and read-only inspection can never
# be an OrphanAction: blocking them is what dead-ended a user whose old
# goal was settled — not even SessionNew or TodoWrite could run.
GATE_EXEMPT_TOOLS = frozenset({
    # session management
    "SessionNew", "SessionList", "SessionSwitch",
    # planning
    "TodoWrite", "TodoRead", "EnterPlanMode", "ExitPlanMode",
    # read-only inspection
    "read_file", "list_dir", "list_directory", "glob_files", "grep",
    "search_files", "file_info", "web_search", "web_fetch",
    "Read", "Glob", "Grep", "WebFetch", "WebSearch",
})


def gate_exempt(tool_name: str) -> bool:
    """True for tools the goal attribution gate must never block."""
    name = str(tool_name or "")
    return name in GATE_EXEMPT_TOOLS or name.startswith("Session")


class GoalContractError(ValueError):
    """Raised when a contract violates the Goal Mode rules."""


def _contract_id(statement: str, clauses: list) -> str:
    payload = json.dumps({"statement": statement, "clauses": clauses},
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


def proof_confidence(proof: dict | None) -> float:
    """The confidence a clause earns when its proof predicate passes."""
    if not proof:
        return 0.0
    return PROOF_CONFIDENCE.get(str(proof.get("type", "")), 0.50)


# ---------------------------------------------------------------------------
# GoalStatus — the live, fold-derived view of the contract
# ---------------------------------------------------------------------------

@dataclass
class ClauseState:
    id: str
    text: str
    kind: str = "OUTCOME"
    weight: float = 0.0
    proof: dict | None = None
    advisory: bool = False
    state: str = "OPEN"          # OPEN | PROVEN | REGRESSED | WAIVED
    confidence: float = 0.0      # strength of the accepted proof
    evidence_seq: int | None = None
    attributed_cost: float = 0.0


@dataclass
class GoalStatus:
    """A derived, never-authoritative snapshot of the goal contract."""
    active: bool = False
    contract_id: str = ""
    statement: str = ""
    clauses: list[ClauseState] = field(default_factory=list)
    anti: list[dict] = field(default_factory=list)
    invariants: list[dict] = field(default_factory=list)
    budget: dict = field(default_factory=dict)
    closing_rule: str = "ALL"
    threshold: float = 1.0
    distance: float = 1.0
    velocity: float = 0.0        # distance reduction per 10 steps
    eta_steps: int | None = None
    focus: str | None = None     # clause id currently under gravity focus
    focus_history: list[str] = field(default_factory=list)
    drift: str = ""
    complete: bool = False
    closed_state: str | None = None

    @property
    def proven_weight(self) -> float:
        return sum(c.weight * c.confidence for c in self.clauses
                   if c.state == "PROVEN")

    @property
    def progress(self) -> float:
        """Share of the contract that is DONE, by clause state (0..1).

        This is what the user sees (goal bar, /state, the compass). It
        is deliberately not ``1 - distance``: distance (§39.1) weights
        each proof by its confidence, so a contract whose clauses are
        ALL PROVEN by file_exists (0.70) read "70%" forever. Progress
        counts PROVEN/WAIVED clause weight; advisory clauses only count
        when the contract has nothing else."""
        if not self.clauses:
            return 0.0
        scope = [c for c in self.clauses if not c.advisory] or self.clauses
        total = sum(max(0.0, c.weight) for c in scope)
        done = [c for c in scope if c.state in ("PROVEN", "WAIVED")]
        if total <= 0:
            return len(done) / len(scope)
        return max(0.0, min(1.0, sum(max(0.0, c.weight) for c in done)
                            / total))

    @property
    def settled(self) -> bool:
        """Every clause PROVEN or WAIVED — nothing left to serve."""
        return bool(self.clauses) and all(
            c.state in ("PROVEN", "WAIVED") for c in self.clauses)

    @property
    def closed(self) -> bool:
        """A goal.closed event sealed a terminal state (§42)."""
        return self.closed_state in TERMINAL_STATES

    @property
    def live(self) -> bool:
        """Active and not yet closed/archived — the only state in which
        a contract is shown to the model/user or gates tool calls."""
        return self.active and not self.closed

    def clause(self, clause_id: str) -> ClauseState | None:
        for c in self.clauses:
            if (c.id.lower() == clause_id.lower()
                    or c.text.lower() == clause_id.lower()):
                return c
        return None


# ---------------------------------------------------------------------------
# GoalContract — writes goal events, reads them back via the fold
# ---------------------------------------------------------------------------

class GoalContract:
    """Define, track, and close a goal over the shared event log."""

    def __init__(self, log: EventLog, judge=None) -> None:
        self.log = log
        self.judge = judge  # fullagent.judge.Judge — required for proofs
        # incremental contract scan: (branch, log cursor, {session:
        # [last goal.set seq, contract, goal-relevant events since it,
        # latest goal.closed state]})
        self._scan_cache: tuple | None = None
        # auto_close is reached from parallel tool workers — one seal only
        self._close_lock = threading.Lock()

    _RELEVANT = frozenset({"clause.proven", "clause.regressed",
                           "clause.waived", "goal.focus"})

    def _session_scan(self) -> tuple[int, dict | None,
                                     tuple[Event, ...], str | None]:
        """(last goal.set seq, its contract, goal-relevant events after
        it, latest goal.closed state after it) for the CURRENT SESSION
        on the current branch.

        SCOPE: the event log is one persistent file shared by every
        session ever run (APP_DIR/eventlog.jsonl). A goal belongs to the
        session that set it — reading "the latest goal.set in the log"
        let a brand-new session (fresh start, /new, SessionNew, a switch)
        inherit some earlier run's contract, inject it into the live
        context and gate every tool call on it. Contracts and their
        proofs are therefore bucketed by Event.session and only the
        bucket of ``log.session`` is ever read.

        PERF: status() runs several times per tool iteration (goal tick,
        measure, reaim, gravity, cost attribution) and each call used to
        walk the entire retained log — 2-4 full walks of up to thousands
        of events, per call, so a goal-mode session slowed down turn
        after turn. The scan is now extended incrementally from the
        previous cursor (O(new events)); a rewind/checkout, or more than
        a window of appends between calls, rebuilds it from scratch."""
        log = self.log
        with log._lock:
            br = log.branch
            sc = self._scan_cache
            delta = None
            if sc is not None and sc[0] == br:
                delta = log.delta_since(sc[1], br)
            if delta is None:
                buckets: dict[str, list] = {}
                evs = log.events(br)
            else:
                buckets = sc[2]
                evs = delta
            rel = self._RELEVANT
            for ev in evs:
                t = ev.type
                sess = ev.session or ""
                if t == "goal.set":
                    data = ev.data if isinstance(ev.data, dict) else {}
                    buckets[sess] = [ev.seq, data, [], None]
                    continue
                b = buckets.get(sess)
                if b is None:
                    continue  # no contract in that session — irrelevant
                if t in rel:
                    b[2].append(ev)
                elif t == "goal.closed":
                    d = ev.data if isinstance(ev.data, dict) else {}
                    b[3] = d.get("state")
            self._scan_cache = (br, log.cursor(br), buckets)
            b = buckets.get(getattr(log, "session", "") or "")
            if b is None:
                return -1, None, (), None
            # a snapshot: the cached list keeps growing under later calls
            # (the TUI polls status() from a background thread)
            return b[0], b[1], tuple(b[2]), b[3]

    def _contract_scan(self) -> tuple[int, tuple[Event, ...], str | None]:
        """(last goal.set seq, goal-relevant events after it, latest
        goal.closed state after it) — current session only."""
        last_set, _, relevant, closed = self._session_scan()
        return last_set, relevant, closed

    def contract(self) -> dict | None:
        """The current session's raw frozen contract (None if none)."""
        g = self._session_scan()[1]
        if not g or not (g.get("statement") or g.get("clauses")):
            return None
        return g

    # -- contract construction (§37) -----------------------------------------

    @staticmethod
    def validate(clauses: list[dict], anti: list[dict],
                 invariants: list[dict], closing_rule: str) -> list[dict]:
        """Enforce the Goal Mode rules at load time (Appendix B). Returns
        the normalised clause list; raises GoalContractError naming the
        offending clause."""
        if not isinstance(clauses, (list, tuple)) or not clauses:
            raise GoalContractError("a contract needs at least one clause")
        if closing_rule not in CLOSING_RULES:
            raise GoalContractError(
                f"closing_rule must be one of {CLOSING_RULES}")
        if not isinstance(anti, list) or not isinstance(invariants, list):
            raise GoalContractError(
                "anti-clauses and invariants must be lists")
        norm: list[dict] = []
        total = 0.0
        seen_ids: set[str] = set()
        for i, c in enumerate(clauses):
            if not isinstance(c, dict):
                raise GoalContractError(
                    f"clause C{i + 1}: must be an object, "
                    f"got {type(c).__name__}")
            cid = str(c.get("id") or f"C{i + 1}")
            text = str(c.get("text", "")).strip()
            if not text:
                raise GoalContractError(f"clause {cid}: empty text")
            # proofs and waivers are keyed by clause id — duplicates would
            # make one twin unprovable and waive/prove BOTH together
            if cid.lower() in seen_ids:
                raise GoalContractError(
                    f"duplicate clause id {cid!r} — ids must be unique")
            seen_ids.add(cid.lower())
            kind = str(c.get("kind", "OUTCOME")).upper()
            if kind not in CLAUSE_KINDS:
                raise GoalContractError(
                    f"clause {cid}: kind must be one of {CLAUSE_KINDS}")
            proof = c.get("proof")
            advisory = bool(c.get("advisory", False))
            if not advisory:
                # §37.2 hard rule: no predicate, no clause
                if not isinstance(proof, dict) or not proof.get("type"):
                    raise GoalContractError(
                        f"clause {cid} has no machine-checkable proof — "
                        "give it a predicate or mark it advisory")
                ptype = str(proof.get("type"))
                if ptype not in PROOF_CONFIDENCE:
                    raise GoalContractError(
                        f"clause {cid}: unknown proof type {ptype!r} — "
                        f"valid: {sorted(PROOF_CONFIDENCE)}")
                if ptype == "model_judgement":
                    raise GoalContractError(
                        f"clause {cid}: model_judgement cannot be the only "
                        "evidence — pair it with a deterministic proof or "
                        "mark the clause advisory")
            weight = c.get("weight", 1.0)
            try:
                weight = float(weight)
            except (TypeError, ValueError):
                raise GoalContractError(
                    f"clause {cid}: weight must be numeric, got {weight!r}")
            if weight != weight or weight in (float("inf"), float("-inf")):
                raise GoalContractError(
                    f"clause {cid}: weight must be finite, got {weight!r}")
            if weight < 0:
                raise GoalContractError(f"clause {cid}: negative weight")
            total += weight
            norm.append({"id": cid, "text": text, "kind": kind,
                         "weight": weight, "proof": proof,
                         "advisory": advisory})
        if total <= 0:
            raise GoalContractError("clause weights must sum to > 0")
        # auto-normalise weights to 1.0 (Appendix B)
        for c in norm:
            c["weight"] = c["weight"] / total
        for a in anti:
            if not a.get("text"):
                raise GoalContractError("anti-clause missing text")
        for inv in invariants:
            if not inv.get("check"):
                raise GoalContractError(
                    f"invariant {inv.get('id', '?')}: missing check")
        return norm

    def set_goal(self, statement: str, clauses: list[dict],
                 anti: list[dict] | None = None,
                 invariants: list[dict] | None = None,
                 budget: dict | None = None,
                 closing_rule: str = "ALL",
                 threshold: float = 1.0,
                 autonomy_ceiling: int = 5) -> dict:
        """Freeze a contract as an event. From this moment the contract is
        history, not configuration — amendments are separate events (§41)."""
        anti = list(anti or [])
        invariants = list(invariants or [])
        norm = self.validate(clauses, anti, invariants, closing_rule)
        try:
            threshold_f = float(threshold)
            autonomy_i = int(autonomy_ceiling)
        except (TypeError, ValueError):
            raise GoalContractError(
                "threshold and autonomy_ceiling must be numeric")
        contract = {
            "id": _contract_id(statement, norm),
            "statement": str(statement),
            "clauses": norm,
            "anti_clauses": anti,
            "invariant_clauses": invariants,
            "budget": dict(budget or {}),
            "closing_rule": closing_rule,
            "threshold": threshold_f,
            "autonomy_ceiling": autonomy_i,
            "version": 1,
            "created_ts": time.time(),
        }
        self.log.append("goal.set", contract, actor="human",
                        provenance="user")
        return contract

    def clear(self) -> None:
        """Deactivate the goal (empty contract event)."""
        self.log.append("goal.set", {"statement": "", "clauses": [],
                                     "anti_clauses": [],
                                     "invariant_clauses": [],
                                     "created_ts": time.time()},
                        actor="human", provenance="user")

    # -- proofs (§39) ----------------------------------------------------------

    def prove_clause(self, clause_id: str, passed: bool,
                     proof_type: str, evidence_seq: int | None = None,
                     detail: str = "") -> bool:
        """Record a proof for a clause. ONLY a passing Judge predicate (or
        an explicit human approval) may prove a clause — the binary proven
        flag is never set from model output (§39.1)."""
        st = self.status()
        c = st.clause(clause_id)
        if c is None or not st.active:
            return False
        if not passed:
            # a failed proof is evidence of NOT-done; record as regression
            # only if it was previously proven
            if c.state == "PROVEN":
                self.log.append("clause.regressed",
                                {"clause": c.id, "reason": detail or
                                 f"{proof_type} proof now fails"})
            return False
        confidence = PROOF_CONFIDENCE.get(proof_type, 0.50)
        self.log.append("clause.proven",
                        {"clause": c.id, "confidence": confidence,
                         "proof_type": proof_type,
                         "evidence_seq": evidence_seq, "detail": detail},
                        actor="judge", provenance="tool_output")
        return True

    def waive(self, clause_id: str, reason: str) -> bool:
        """Human waiver — recorded as an event, never silent (§39.3)."""
        st = self.status()
        c = st.clause(clause_id)
        if c is None or not st.active:
            return False
        self.log.append("clause.waived", {"clause": c.id, "reason": reason},
                        actor="human", provenance="user")
        return True

    def prove_by_predicate(self, clause_id: str) -> tuple[bool, str]:
        """Run the clause's OWN predicate through the Judge (§40.2 VII:
        the clause and the test are the same object). Returns (ok, detail)."""
        if self.judge is None:
            return False, "no judge attached"
        st = self.status()
        c = st.clause(clause_id)
        if c is None or not st.active:
            return False, f"no such clause: {clause_id}"
        if not c.proof:
            return False, f"clause {c.id} is advisory — no predicate to run"
        verdict = self.judge.check(c.proof)
        ev_seq = self.log.head()
        self.prove_clause(c.id, verdict.passed, str(c.proof.get("type")),
                          evidence_seq=ev_seq, detail=verdict.detail)
        return verdict.passed, verdict.detail

    # -- anti-clauses & invariants (§37.4) --------------------------------------

    def _is_regressed_open(self, clause_id: str) -> bool:
        """Is there an un-cleared clause.regressed for this clause in the
        current contract? check_anti_clauses()/check_invariants() run
        after every write event — without this, a persistent violation
        seals a duplicate regressed event per write (pure log spam; the
        fold only ever reads the latest one)."""
        last_set, evs, _ = self._contract_scan()
        for ev in reversed(evs):
            if ev.seq <= last_set:
                break
            if ev.type == "clause.regressed" and \
                    ev.data.get("clause") == clause_id:
                return True
            if ev.type in ("clause.proven", "clause.waived") and \
                    ev.data.get("clause") == clause_id:
                return False
        return False

    def check_anti_clauses(self) -> list[dict]:
        """Re-check every anti-clause NOW. Called after every write event.
        An anti-clause's check describes the condition that must HOLD; if
        the predicate fails, the forbidden thing happened -> violation."""
        if self.judge is None:
            return []
        st = self.status()
        violations: list[dict] = []
        for anti in st.anti:
            check = anti.get("check")
            if not isinstance(check, dict) or not check.get("type"):
                continue
            verdict = self.judge.check(check)
            if not verdict.passed:
                cid = anti.get("id", "?")
                violations.append({"clause": cid,
                                   "text": anti.get("text", ""),
                                   "detail": verdict.detail})
                if self._is_regressed_open(cid):
                    continue  # already recorded and still open — no dup
                self.log.append("clause.regressed",
                                {"clause": cid,
                                 "anti": True,
                                 "reason": verdict.detail},
                                actor="judge", provenance="tool_output")
        return violations

    def check_invariants(self) -> list[dict]:
        """Re-check invariant clauses (must remain true throughout)."""
        if self.judge is None:
            return []
        st = self.status()
        violations: list[dict] = []
        for inv in st.invariants:
            check = inv.get("check")
            if not isinstance(check, dict) or not check.get("type"):
                continue
            verdict = self.judge.check(check)
            if not verdict.passed:
                cid = inv.get("id", "?")
                violations.append({"clause": cid,
                                   "text": inv.get("text", ""),
                                   "detail": verdict.detail})
                if self._is_regressed_open(cid):
                    continue  # already recorded and still open — no dup
                self.log.append("clause.regressed",
                                {"clause": cid,
                                 "invariant": True,
                                 "reason": verdict.detail},
                                actor="judge", provenance="tool_output")
        return violations

    # -- distance, velocity, drift (§39) ----------------------------------------

    def distance(self) -> float:
        return self.status().distance

    def measure(self) -> dict:
        """Recompute distance + velocity and seal a goal.distance event
        (§38.3 step 5-7). Pure arithmetic over the fold — zero tokens."""
        st = self.status()
        if not st.active:
            return {"distance": 1.0, "velocity": 0.0}
        last_set_seq = self._contract_scan()[0]
        # newest measure of THIS contract. Measures are sealed in log
        # order with a non-decreasing "seq", so walk back from the end and
        # stop at the first older one — O(1) instead of filtering the
        # whole (session-long) measure history on every tick.
        last = None
        for m in reversed(fold(self.log).distance_measures):
            if not isinstance(m, dict):
                continue  # corrupt record — never crash the goal tick
            m_seq = m.get("seq", 0)
            if isinstance(m_seq, bool) or not isinstance(m_seq, (int, float)):
                m_seq = 0
            if m_seq > last_set_seq:
                last = m
            break
        velocity = 0.0
        if last is not None:
            try:
                steps = max(1, st_head_delta(self.log,
                                             int(last.get("seq", 0))))
                velocity = (float(last.get("distance", 1.0)) - st.distance) \
                    / steps * 10.0
            except (TypeError, ValueError):
                velocity = 0.0
        record = {"distance": st.distance, "velocity": velocity,
                  "seq": self.log.head(), "focus": st.focus}
        self.log.append("goal.distance", record)
        return record

    # -- gravity & focus (§40) ----------------------------------------------------

    def gravity(self) -> dict[str, float]:
        """Score every open clause at rung 1 (§40.1). The highest-gravity
        clause becomes the focus; everything re-aims at it."""
        st = self.status()
        # SPEED: the fold ran once per clause (inside the loop) — hoisted.
        last_set_seq, relevant, _ = self._contract_scan()
        regressed_ids = set()
        for ev in relevant:
            if ev.type != "clause.regressed":
                continue
            if ev.seq <= last_set_seq:
                continue
            d = ev.data or {}
            if d.get("anti") or d.get("invariant"):
                continue
            regressed_ids.add(d.get("clause"))
        scores: dict[str, float] = {}
        for c in st.clauses:
            if c.state in ("PROVEN", "WAIVED"):
                scores[c.id] = 0.0
                continue
            weight = c.weight
            feasibility = 1.0 if c.proof else 0.3  # advisory = low pull
            est_cost = max(0.05, c.attributed_cost or 0.05)
            unblocked = 1.0
            # freshness penalty: recently regressed clauses pull less
            freshness = 0.5 if c.id in regressed_ids else 1.0
            scores[c.id] = (weight * feasibility * (1.0 / est_cost)
                            * unblocked * freshness)
        return scores

    def reaim(self, reason: str = "gravity") -> str | None:
        """Point the run at the highest-gravity open clause. A focus shift
        is an event, so the attention history is replayable (§40.3)."""
        st = self.status()
        if not st.active:
            return None
        scores = self.gravity()
        open_scores = {k: v for k, v in scores.items() if v > 0}
        if not open_scores:
            return None
        best = max(open_scores, key=lambda k: (open_scores[k], k))
        if best != st.focus:
            self.log.append("goal.focus",
                            {"from": st.focus, "to": best, "reason": reason,
                             "gravity": open_scores[best]},
                            actor="navigator")
        return best

    # -- amendments (§41) -----------------------------------------------------------

    def propose_amendment(self, kind: str, rationale: str,
                          impact: str = "") -> str:
        """The agent may NEVER edit the contract — only propose (§41.2).
        Returns the proposal id. Rejected proposals are kept: they record
        where the agent's understanding diverged from the human's."""
        kinds = ("ADD_CLAUSE", "SPLIT", "REWEIGHT", "WAIVE", "RELAX_PROOF",
                 "EXTEND_BUDGET")
        if kind not in kinds:
            raise GoalContractError(f"amendment kind must be one of {kinds}")
        proposal_id = hashlib.sha256(
            f"{kind}:{rationale}:{time.time()}".encode()).hexdigest()[:10]
        self.log.append("goal.amendment",
                        {"proposal": proposal_id, "kind": kind,
                         "rationale": rationale, "impact": impact,
                         "verdict": "pending"},
                        actor="sovereign", provenance="model")
        return proposal_id

    def resolve_amendment(self, proposal_id: str, verdict: str) -> bool:
        """Human accepts or rejects a pending amendment (the decision is an
        event; the pending record is re-emitted with its verdict)."""
        if verdict not in ("accepted", "rejected"):
            return False
        for am in fold(self.log).amendments:
            if am.get("proposal") == proposal_id and \
                    am.get("verdict") == "pending":
                self.log.append("goal.amendment",
                                {**{k: v for k, v in am.items()
                                    if k != "verdict"},
                                 "verdict": verdict,
                                 "of_proposal": proposal_id},
                                actor="human", provenance="user")
                return True
        return False

    # -- closure (§42) ---------------------------------------------------------------

    def closure_check(self, st: GoalStatus | None = None
                      ) -> tuple[str, list[str]]:
        """Compute the terminal state from proof events — never declared by
        a model (§42.1). Returns (state, reasons)."""
        st = st if st is not None else self.status()
        if not st.active:
            return ABANDONED, ["no active contract"]
        reasons: list[str] = []
        non_advisory = [c for c in st.clauses if not c.advisory]
        proven = [c for c in non_advisory if c.state == "PROVEN"]
        waived = [c for c in non_advisory if c.state == "WAIVED"]

        # §39.3 hard rule: no ACHIEVED on model-judgement-only evidence
        weak = [c for c in proven if c.confidence < MIN_PROOF_CONFIDENCE]
        if weak:
            reasons.append("weak proof (<0.85 confidence) for: "
                           + ", ".join(c.id for c in weak))
        missing = [c for c in non_advisory
                   if c.state not in ("PROVEN", "WAIVED")]
        if missing:
            reasons.append("open clauses: "
                           + ", ".join(c.id for c in missing))

        proven_w = sum(c.weight for c in proven) + \
            sum(c.weight for c in waived)
        if not missing and not weak:
            return ACHIEVED, reasons
        if st.closing_rule == "WEIGHTED_THRESHOLD" and \
                proven_w >= st.threshold and not weak:
            return PARTIAL, reasons
        if not missing and weak:
            return PARTIAL, reasons
        return STALLED if not missing else BLOCKED if any(
            c.state == "OPEN" and not c.proof for c in missing) else STALLED, \
            reasons

    def close(self, fresh: bool = True, auto: bool = False) -> dict:
        """The closure ritual (§42.2): re-prove every clause from scratch,
        re-check anti-clauses, then compute the terminal state and seal a
        goal.closed event. Returns {state, reasons, bundle}."""
        st = self.status()
        if not st.active:
            return {"state": ABANDONED, "reasons": ["no active contract"],
                    "bundle": ""}
        if fresh and self.judge is not None:
            for c in st.clauses:
                if c.proof and not c.advisory:
                    self.prove_by_predicate(c.id)
            self.check_anti_clauses()
            self.check_invariants()
            # the re-proofs sealed new events — the pre-loop status is
            # stale; refresh once and reuse it below (was: status()
            # recomputed separately by closure_check and evidence_bundle)
            st = self.status()
        state, reasons = self.closure_check(st)
        data = {"state": state, "reasons": reasons}
        if auto:
            data["auto"] = True
        self.log.append("goal.closed", data, actor="kernel")
        return {"state": state, "reasons": reasons,
                "bundle": self.evidence_bundle(st)}

    def auto_close(self) -> str | None:
        """Archive a SETTLED contract (every clause PROVEN or WAIVED) by
        sealing its goal.closed event. Returns the terminal state, or
        None when there is nothing to close.

        Without this a fully proven contract stayed open forever: it kept
        riding in the live context, and with no open clause left every
        tool call was rejected as an OrphanAction — the user's next
        request could not run at all. No re-proof (fresh=False): each
        clause was already proven by its own Judge predicate, and
        closure_check still grades weak proofs as PARTIAL (§39.3)."""
        with self._close_lock:
            st = self.status()
            if not st.active or st.closed or not st.settled:
                return None
            return self.close(fresh=False, auto=True)["state"]

    def evidence_bundle(self, st: GoalStatus | None = None) -> str:
        """§42.3 — the deliverable of Goal Mode: a proof per clause with
        event ids, not a paragraph claiming success."""
        st = st if st is not None else self.status()
        if not st.active:
            return "no active contract"
        # The header must agree with the closure state (§42.1), which is
        # stricter than `complete` (it also demands proof confidence).
        state, _ = self.closure_check(st)
        lines = [f"GOAL {state} — contract {st.contract_id}",
                 f'  "{st.statement}"', ""]
        for c in st.clauses:
            mark = {"PROVEN": "PROVEN", "WAIVED": "WAIVED",
                    "REGRESSED": "REGRESSED"}.get(c.state, "OPEN  ")
            conf = f"conf {c.confidence:.2f}" if c.state == "PROVEN" else "        "
            ev = f"ev #{c.evidence_seq}" if c.evidence_seq is not None else ""
            lines.append(f" {c.id} [{c.kind:<9} w{c.weight:.2f}] {c.text:<32} "
                         f"{mark} {conf} {ev}")
        for a in st.anti:
            lines.append(f" {a.get('id', '?')} [ANTI     ] "
                         f"{a.get('text', '')}")
        for inv in st.invariants:
            lines.append(f" {inv.get('id', '?')} [INVARIANT] "
                         f"{inv.get('text', '')}")
        lines.append("")
        lines.append(f" distance {st.distance:.2f}   velocity "
                     f"{st.velocity:+.3f}/10 steps")
        if st.focus_history:
            lines.append(" focus history  " + " -> ".join(st.focus_history))
        return "\n".join(lines)

    # -- reads (always via the fold) ----------------------------------------------

    def status(self) -> GoalStatus:
        """Fold the log and compute the live GoalStatus (§39 formula)."""
        # SCOPE: the contract of THIS session only (see _session_scan) —
        # fold().goal is the latest goal.set of ANY session in the shared
        # log, which is how an old run's goal leaked into a new session.
        last_set_seq, g, relevant, scanned_closed = self._session_scan()
        if not g or not (g.get("statement") or g.get("clauses")):
            return GoalStatus()
        st = fold(self.log)
        clauses_raw = g.get("clauses") or []
        # proof history, walked with event seqs: latest proof per clause
        # wins; a regression AFTER a proof reopens the clause (§42.4).
        # Only events AFTER the current goal.set count — proofs from a
        # previous contract must not leak into this one.
        # SPEED: one log walk collects last_set_seq + buffers the small
        # set of goal-relevant events; the filter then runs over the
        # buffer, not the log (was: three full log walks).
        proven: dict[str, tuple[int, dict]] = {}
        regressed_after: dict[str, int] = {}
        waived: set[str] = set()
        focus_history: list[str] = []
        for ev in relevant:
            if ev.seq <= last_set_seq:
                continue
            d = ev.data
            if ev.type == "clause.proven":
                proven[d.get("clause", "")] = (ev.seq, d)
            elif ev.type == "clause.regressed":
                if d.get("anti") or d.get("invariant"):
                    continue  # anti/invariant regressions don't reopen clauses
                cid = d.get("clause", "")
                regressed_after[cid] = max(regressed_after.get(cid, -1),
                                           ev.seq)
            elif ev.type == "clause.waived":
                waived.add(d.get("clause", ""))
            elif ev.type == "goal.focus" and d.get("to"):
                focus_history.append(d.get("to"))

        clauses: list[ClauseState] = []
        for raw in clauses_raw:
            cid = str(raw.get("id", ""))
            cs = ClauseState(id=cid, text=str(raw.get("text", "")),
                             kind=str(raw.get("kind", "OUTCOME")),
                             weight=float(raw.get("weight", 0.0)),
                             proof=raw.get("proof"),
                             advisory=bool(raw.get("advisory", False)))
            if cid in waived:
                cs.state = "WAIVED"
                cs.confidence = 1.0
            elif cid in proven:
                p_seq, p = proven[cid]
                if regressed_after.get(cid, -1) > p_seq:
                    cs.state = "REGRESSED"
                else:
                    cs.state = "PROVEN"
                    cs.confidence = float(p.get("confidence", 0.0))
                    cs.evidence_seq = p.get("evidence_seq")
            clauses.append(cs)

        # §39.1 distance = 1 - Σ weight * confidence * proven
        distance = 1.0 - sum(c.weight * c.confidence for c in clauses
                             if c.state in ("PROVEN", "WAIVED"))
        distance = max(0.0, min(1.0, distance))

        # velocity + ETA from sealed goal.distance measures
        measures = st.distance_measures
        last_m = measures[-1] if measures else None
        m_seq = last_m.get("seq", 0) if isinstance(last_m, dict) else 0
        # only a measure of THIS contract (another session's goal may
        # have been measured last in the shared log)
        velocity = (last_m.get("velocity", 0.0)
                    if isinstance(last_m, dict)
                    and isinstance(m_seq, (int, float))
                    and not isinstance(m_seq, bool)
                    and m_seq > last_set_seq else 0.0)
        if isinstance(velocity, bool) or \
                not isinstance(velocity, (int, float)):
            velocity = 0.0
        eta = None
        if isinstance(velocity, (int, float)) and velocity > 1e-6:
            eta = int(distance / (velocity / 10.0))

        # focus = latest goal.focus in the current contract (already
        # collected in the single merged walk above)
        focus = focus_history[-1] if focus_history else None

        # drift: >=30% of attributed cost on the lowest-weight open clause
        drift = ""
        open_c = [c for c in clauses if c.state == "OPEN"]
        if len(open_c) >= 2:
            total_cost = sum(c.attributed_cost for c in clauses) or 0.0
            if total_cost > 0:
                lowest = min(open_c, key=lambda c: c.weight)
                if lowest.attributed_cost / total_cost >= 0.30:
                    drift = (f"spend concentrated on low-weight clause "
                             f"{lowest.id} (w{lowest.weight:.2f})")

        non_advisory = [c for c in clauses if not c.advisory]
        complete = bool(non_advisory) and all(
            c.state in ("PROVEN", "WAIVED") for c in non_advisory)

        # the latest goal.closed AFTER this session's goal.set — a stale
        # close from a previous contract (or another session's close)
        # can't leak through
        closed_state = scanned_closed

        return GoalStatus(
            active=True,
            contract_id=str(g.get("id", "")),
            statement=str(g.get("statement", "")),
            clauses=clauses,
            anti=[dict(a) for a in (g.get("anti_clauses") or [])],
            invariants=[dict(i) for i in (g.get("invariant_clauses") or [])],
            budget=dict(g.get("budget") or {}),
            closing_rule=str(g.get("closing_rule", "ALL")),
            threshold=float(g.get("threshold", 1.0)),
            distance=distance,
            velocity=float(velocity or 0.0),
            eta_steps=eta,
            focus=focus,
            focus_history=focus_history,
            drift=drift,
            complete=complete,
            closed_state=closed_state,
        )

    def format(self) -> str:
        """The Goal Compass as text (§43.1)."""
        s = self.status()
        if not s.active:
            return "GOAL: none"
        bar_w = 24
        filled = int(round(s.progress * bar_w))
        bar = "█" * filled + "░" * (bar_w - filled)
        closed = f"  [{s.closed_state}]" if s.closed else ""
        lines = [f'GOAL "{s.statement}"  contract {s.contract_id}{closed}',
                 f" distance {s.distance:.2f} [{bar}] "
                 f"{s.progress * 100:.0f}% proven"
                 f"   velocity {s.velocity:+.3f}/10 steps"
                 + (f"   ETA ~{s.eta_steps} steps" if s.eta_steps else "")]
        for c in s.clauses:
            focus_mark = "  <-- FOCUS" if c.id == s.focus else ""
            adv = " (advisory)" if c.advisory else ""
            conf = f" {c.confidence:.2f}" if c.state == "PROVEN" else ""
            lines.append(f"  {c.id} w{c.weight:.2f} [{c.kind:<9}] "
                         f"{c.text:<30} {c.state}{conf}{adv}{focus_mark}")
        for a in s.anti:
            lines.append(f"  {a.get('id', '?')} [ANTI] {a.get('text', '')}")
        for inv in s.invariants:
            lines.append(f"  {inv.get('id', '?')} [INV ] {inv.get('text', '')}")
        if s.focus_history:
            lines.append(" focus: " + " -> ".join(s.focus_history))
        if s.drift:
            lines.append(f" DRIFT: {s.drift}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# fold helpers
# ---------------------------------------------------------------------------

def progress_bar(st: GoalStatus | None, width: int = 5) -> str | None:
    """The compact goal meter for status bars: "▰▰▰▱▱ 60%", or None when
    there is no live goal (none, another session's, or closed/archived).
    Driven by clause states (GoalStatus.progress), so a contract whose
    clauses are all PROVEN reads 100% — never the confidence-weighted
    1-distance (e.g. 70% for file_exists proofs)."""
    if st is None or not getattr(st, "active", False):
        return None
    if getattr(st, "closed_state", None) in TERMINAL_STATES:
        return None
    pct = max(0.0, min(100.0, float(st.progress) * 100.0))
    filled = max(0, min(width, int(round(pct / 100.0 * width))))
    return "▰" * filled + "▱" * (width - filled) + f" {pct:.0f}%"


def st_head_delta(log: EventLog, since_seq: int) -> int:
    return max(1, log.head() - since_seq)


def _last_closed_state(log: EventLog) -> str | None:
    # Only a goal.closed AFTER the latest goal.set counts — a close from
    # a previous contract must not leak into the current open one.
    last_set_seq = -1
    for e in log.events():
        if e.type == "goal.set":
            last_set_seq = e.seq
    for e in reversed(log.events()):
        if e.type == "goal.closed" and e.seq > last_set_seq:
            return e.data.get("state")
    return None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    from .judge import Judge

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        log = EventLog(tmp / "goal.jsonl")
        judge = Judge(log)
        gc = GoalContract(log, judge)

        sample = tmp / "src.py"
        sample.write_text("def verify_token(leeway=0):\n    return True\n")

        # -- validation rules (Appendix B) ------------------------------------
        try:
            gc.set_goal("bad", [{"id": "C1", "text": "vague clause"}])
            raise AssertionError("clause without proof must be rejected")
        except GoalContractError as e:
            assert "C1" in str(e)

        try:
            gc.set_goal("bad", [{"id": "C1", "text": "x",
                                 "proof": {"type": "model_judgement"}}])
            raise AssertionError("model_judgement-only must be rejected")
        except GoalContractError:
            pass

        try:
            gc.set_goal("bad", [{"id": "C1", "text": "x", "kind": "NOPE",
                                 "proof": {"type": "file_exists",
                                           "path": str(sample)}}])
            raise AssertionError("bad kind must be rejected")
        except GoalContractError:
            pass

        # advisory clauses are allowed without proof
        contract = gc.set_goal(
            "make verify_token configurable",
            [{"id": "C1", "text": "src.py exists", "kind": "ARTIFACT",
              "weight": 0.5,
              "proof": {"type": "file_exists", "path": str(sample)}},
             {"id": "C2", "text": "leeway parameter exists",
              "kind": "OUTCOME", "weight": 0.5,
              "proof": {"type": "ast_assert", "path": str(sample),
                        "symbol": "verify_token",
                        "has_parameter": "leeway"}}],
            anti=[{"id": "A1", "text": "src.py must not be deleted",
                   "check": {"type": "file_exists", "path": str(sample)}}],
            invariants=[{"id": "I1", "text": "src.py stays valid python",
                         "check": {"type": "exit_code",
                                   "command": f"python -c 'import ast;"
                                              f"ast.parse(open(\"{sample}\").read())'",
                                   "expect": 0}}],
        )
        assert contract["id"]
        # weights auto-normalised
        assert abs(sum(c["weight"] for c in contract["clauses"]) - 1.0) < 1e-9

        st = gc.status()
        assert st.active and len(st.clauses) == 2
        assert abs(st.distance - 1.0) < 1e-9  # nothing proven yet

        # -- proving via the clause's own predicate ---------------------------
        ok, detail = gc.prove_by_predicate("C1")
        assert ok, detail
        ok, detail = gc.prove_by_predicate("C2")
        assert ok, detail
        st = gc.status()
        assert st.complete, st
        # distance = 1 - (0.5*0.70 + 0.5*0.85) = 0.225 exactly (§39.1)
        assert abs(st.distance - 0.225) < 1e-9, st.distance
        c1 = st.clause("C1")
        assert c1.state == "PROVEN" and abs(c1.confidence - 0.70) < 1e-9

        # -- closure ritual ----------------------------------------------------
        result = gc.close(fresh=True)
        assert result["state"] == PARTIAL, result  # C1 conf 0.70 < 0.85
        assert any("weak proof" in r for r in result["reasons"]), result
        assert "contract" in result["bundle"]

        # human waiver of the weak-proof clause upgrades closure to ACHIEVED
        assert gc.waive("C1", "human verified the file by eye")
        result = gc.close(fresh=False)
        assert result["state"] == ACHIEVED, result

        # -- anti-clause violation reopens and blocks closure ------------------
        gc2 = GoalContract(EventLog(tmp / "goal2.jsonl"), judge)
        f2 = tmp / "keep.txt"
        f2.write_text("keep me")
        gc2.set_goal(
            "work near keep.txt",
            [{"id": "C1", "text": "work done", "weight": 1.0,
              "proof": {"type": "file_exists", "path": str(f2)}}],
            anti=[{"id": "A1", "text": "keep.txt must survive",
                   "check": {"type": "file_exists", "path": str(f2)}}])
        gc2.prove_by_predicate("C1")
        assert gc2.check_anti_clauses() == []
        f2.unlink()  # the forbidden thing happens
        violations = gc2.check_anti_clauses()
        assert len(violations) == 1 and violations[0]["clause"] == "A1"

        # -- gravity & focus ----------------------------------------------------
        gc3 = GoalContract(EventLog(tmp / "goal3.jsonl"), judge)
        gc3.set_goal(
            "two clauses",
            [{"id": "C1", "text": "small", "weight": 0.2,
              "proof": {"type": "file_exists", "path": str(sample)}},
             {"id": "C2", "text": "big", "weight": 0.8,
              "proof": {"type": "file_exists", "path": str(sample)}}])
        focus = gc3.reaim()
        assert focus == "C2", focus  # higher weight wins
        st = gc3.status()
        assert st.focus == "C2" and st.focus_history == ["C2"]

        # -- amendments ----------------------------------------------------------
        seq = gc3.propose_amendment("EXTEND_BUDGET", "task is larger than "
                                    "expected", "+$1")
        assert gc3.resolve_amendment(seq, "rejected")
        ams = fold(gc3.log).amendments
        assert any(a.get("verdict") == "rejected" for a in ams)

        # -- distance measure events ---------------------------------------------
        rec = gc3.measure()
        assert "distance" in rec and "velocity" in rec

        # -- §38.1/§42: a closed contract stops demanding attribution ------------
        # regression: a fully proven, sealed contract used to leave every tool
        # call blocked as an OrphanAction forever
        gc4 = GoalContract(EventLog(tmp / "goal4.jsonl"), judge)
        f4 = tmp / "done.txt"
        f4.write_text("done")
        gc4.set_goal(
            "single clause",
            [{"id": "C1", "text": "done.txt exists", "weight": 1.0,
              "proof": {"type": "file_exists", "path": str(f4)}}])
        ok, detail = gc4.prove_by_predicate("C1")
        assert ok, detail
        st = gc4.status()
        assert st.complete and st.closed_state is None  # open until sealed
        result = gc4.close(fresh=True)
        st = gc4.status()
        assert st.closed_state == result["state"], (st.closed_state, result)
        assert st.closed_state in TERMINAL_STATES
        assert fold(gc4.log).goal_closed is not None
        # a fresh contract reopens the world: the stale close must not leak
        gc4.set_goal(
            "next job",
            [{"id": "C1", "text": "done.txt exists", "weight": 1.0,
              "proof": {"type": "file_exists", "path": str(f4)}}])
        st = gc4.status()
        assert st.closed_state is None, st.closed_state
        assert fold(gc4.log).goal_closed is None

    print("GOAL SELF-TEST PASS")
