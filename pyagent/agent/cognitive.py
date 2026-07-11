"""Cognitive architectures — SOAR/ACT-R style memory systems.

Implements the three memory systems from cognitive science:
  1. PROCEDURAL MEMORY (SOAR-style) — production rules: if (state) then (action)
  2. DECLARATIVE MEMORY (ACT-R-style) — facts with activation levels (decay, priming)
  3. WORKING MEMORY — limited-capacity (7±2 items), most-recent/most-relevant

Plus:
  * Goal hierarchy (HTN planning)
  * Episodic replay — offline learning from past experiences

These are real cognitive models, not just storage. They model how humans
think: forgetting, priming, chunking, automaticity.
"""
from __future__ import annotations

import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

# ============================================================================
# PROCEDURAL MEMORY (SOAR-style production rules)
# ============================================================================

@dataclass
class ProductionRule:
    """A SOAR-style production rule: IF conditions THEN actions."""
    name: str
    conditions: dict[str, Any]  # state attributes that must match
    actions: list[str]  # actions to fire
    priority: int = 0  # higher = fires first
    firing_count: int = 0  # how many times this rule has fired
    last_fired: float = 0.0


class ProceduralMemory:
    """SOAR-style procedural memory — production rules that fire on matching states."""

    def __init__(self) -> None:
        self.rules: list[ProductionRule] = []
        self._default_rules()

    def _default_rules(self) -> None:
        """Load common-sense procedural rules."""
        self.add_rule(ProductionRule(
            name="if_test_failing_then_debug",
            conditions={"state": "test_failing"},
            actions=["read_error_message", "locate_failing_test", "diagnose_root_cause"],
            priority=10,
        ))
        self.add_rule(ProductionRule(
            name="if_import_error_then_check_deps",
            conditions={"state": "import_error"},
            actions=["check_requirements_file", "pip_install_missing"],
            priority=9,
        ))
        self.add_rule(ProductionRule(
            name="if_syntax_error_then_fix_line",
            conditions={"state": "syntax_error"},
            actions=["read_error_line", "fix_syntax"],
            priority=9,
        ))
        self.add_rule(ProductionRule(
            name="if_file_not_found_then_check_path",
            conditions={"state": "file_not_found"},
            actions=["check_working_directory", "search_for_file"],
            priority=8,
        ))
        self.add_rule(ProductionRule(
            name="if_permission_denied_then_check_perms",
            conditions={"state": "permission_denied"},
            actions=["check_file_permissions", "suggest_chmod_or_sudo"],
            priority=7,
        ))

    def add_rule(self, rule: ProductionRule) -> None:
        self.rules.append(rule)
        self.rules.sort(key=lambda r: -r.priority)

    def fire(self, current_state: dict[str, Any]) -> list[ProductionRule]:
        """Find and fire all rules whose conditions match the current state.

        Returns the list of rules that fired.
        """
        fired: list[ProductionRule] = []
        for rule in self.rules:
            if self._matches(rule.conditions, current_state):
                rule.firing_count += 1
                rule.last_fired = time.time()
                fired.append(rule)
        return fired

    def _matches(self, conditions: dict, state: dict) -> bool:
        """Check if all conditions are satisfied by the current state."""
        for key, value in conditions.items():
            if state.get(key) != value:
                return False
        return True

    def suggest_actions(self, current_state: dict[str, Any]) -> list[str]:
        """Suggest actions based on the current state."""
        fired = self.fire(current_state)
        actions: list[str] = []
        for rule in fired:
            actions.extend(rule.actions)
        return actions

    def dashboard(self) -> str:
        lines = [f"Procedural memory ({len(self.rules)} rules):"]
        for rule in self.rules[:15]:
            fired_str = f"fired {rule.firing_count}x" if rule.firing_count > 0 else "never fired"
            lines.append(f"  [{rule.priority}] {rule.name:<40} ({fired_str})")
        return "\n".join(lines)


# ============================================================================
# DECLARATIVE MEMORY (ACT-R-style with activation)
# ============================================================================

@dataclass
class DeclarativeChunk:
    """An ACT-R-style declarative memory chunk."""
    id: str
    content: str  # the fact/knowledge
    category: str = "general"
    creation_time: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    base_level_activation: float = 0.0  # decays over time, boosted by access
    spreading_activation: float = 0.0  # boosted by related chunk access

    @property
    def total_activation(self) -> float:
        """Total activation = base level + spreading - time decay."""
        decay = 0.5  # ACT-R default decay rate
        time.time() - self.last_accessed
        time_since_creation = time.time() - self.creation_time
        # Base-level learning: log(sum of time^(-decay))
        base = math.log(1 + self.access_count) - decay * math.log(1 + time_since_creation / 3600)
        return base + self.spreading_activation

    @property
    def retrieval_probability(self) -> float:
        """Probability of successful retrieval (logistic function of activation)."""
        # ACT-R retrieval threshold and noise.
        threshold = 0.0
        noise = 0.3
        activation = self.total_activation
        # Logistic function.
        return 1.0 / (1.0 + math.exp(-(activation - threshold) / noise))


class DeclarativeMemory:
    """ACT-R-style declarative memory with activation-based retrieval."""

    def __init__(self) -> None:
        self.chunks: dict[str, DeclarativeChunk] = {}

    def add(self, chunk_id: str, content: str, category: str = "general") -> DeclarativeChunk:
        chunk = DeclarativeChunk(id=chunk_id, content=content, category=category)
        self.chunks[chunk_id] = chunk
        return chunk

    def retrieve(self, chunk_id: str) -> str | None:
        """Retrieve a chunk by ID, updating its activation."""
        chunk = self.chunks.get(chunk_id)
        if chunk is None:
            return None
        chunk.access_count += 1
        chunk.last_accessed = time.time()
        # Check if retrieval succeeds (probabilistic).
        if chunk.retrieval_probability < 0.5:
            return None  # retrieval failure (forgotten)
        return chunk.content

    def recall(self, query: str, top_k: int = 5) -> list[tuple[float, DeclarativeChunk]]:
        """Recall chunks matching a query, ranked by activation."""
        scored: list[tuple[float, DeclarativeChunk]] = []
        query_lower = query.lower()
        for chunk in self.chunks.values():
            # Boost spreading activation for related chunks.
            if query_lower in chunk.content.lower():
                chunk.spreading_activation = 1.0
            else:
                chunk.spreading_activation = 0.0
            # Score by activation + text match.
            match_score = 1.0 if query_lower in chunk.content.lower() else 0.0
            score = chunk.total_activation + match_score
            scored.append((score, chunk))
        scored.sort(key=lambda x: -x[0])
        return scored[:top_k]

    def forget(self, chunk_id: str) -> bool:
        """Remove a chunk (intentional forgetting)."""
        if chunk_id in self.chunks:
            del self.chunks[chunk_id]
            return True
        return False

    def decay_all(self) -> None:
        """Simulate time-based decay (call periodically)."""
        # In ACT-R, decay is computed at retrieval time, so this is a no-op.
        # But we can prune very-low-activation chunks.
        to_remove = [cid for cid, chunk in self.chunks.items() if chunk.retrieval_probability < 0.05]
        for cid in to_remove:
            del self.chunks[cid]

    def dashboard(self) -> str:
        lines = [f"Declarative memory ({len(self.chunks)} chunks):"]
        # Sort by activation.
        sorted_chunks = sorted(self.chunks.values(), key=lambda c: -c.total_activation)
        for chunk in sorted_chunks[:15]:
            retrieval = chunk.retrieval_probability
            icon = "🔥" if retrieval > 0.8 else "📋" if retrieval > 0.5 else "💤" if retrieval > 0.2 else " Forgotten"
            lines.append(f"  {icon} [{chunk.category}] {chunk.content[:50]:<50} (p={retrieval:.0%}, n={chunk.access_count})")
        return "\n".join(lines)


# ============================================================================
# WORKING MEMORY (limited capacity, like human 7±2)
# ============================================================================

@dataclass
class WorkingMemoryItem:
    """An item in working memory."""
    id: str
    content: Any
    relevance: float = 1.0  # 0-1, higher = more relevant
    timestamp: float = field(default_factory=time.time)
    source: str = ""


class WorkingMemory:
    """Limited-capacity working memory (7±2 items, like humans)."""

    def __init__(self, capacity: int = 7) -> None:
        self.capacity = capacity
        self.items: OrderedDict[str, WorkingMemoryItem] = OrderedDict()

    def add(self, item_id: str, content: Any, relevance: float = 1.0, source: str = "") -> None:
        """Add an item. If at capacity, evict the least-relevant item."""
        item = WorkingMemoryItem(id=item_id, content=content, relevance=relevance, source=source)
        if item_id in self.items:
            del self.items[item_id]  # remove old, will re-add at end
        self.items[item_id] = item
        # Evict if over capacity.
        while len(self.items) > self.capacity:
            # Find least-relevant (lowest relevance, oldest if tie).
            least_relevant = min(self.items.values(), key=lambda i: (i.relevance, i.timestamp))
            del self.items[least_relevant.id]

    def get(self, item_id: str) -> Any | None:
        item = self.items.get(item_id)
        if item is None:
            return None
        # Access boosts relevance slightly (priming).
        item.relevance = min(1.0, item.relevance + 0.1)
        return item.content

    def get_all(self) -> list[WorkingMemoryItem]:
        return list(self.items.values())

    def clear(self) -> None:
        self.items.clear()

    def update_relevance(self, item_id: str, relevance: float) -> None:
        if item_id in self.items:
            self.items[item_id].relevance = relevance

    def dashboard(self) -> str:
        lines = [f"Working memory ({len(self.items)}/{self.capacity} slots):"]
        for item in self.items.values():
            lines.append(f"  [{item.relevance:.0%}] {str(item.content)[:60]}")
        return "\n".join(lines)


# ============================================================================
# GOAL HIERARCHY (HTN — Hierarchical Task Network)
# ============================================================================

@dataclass
class GoalNode:
    """A node in the goal hierarchy."""
    id: str
    description: str
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | in_progress | completed | failed
    priority: int = 5
    estimated_effort: float = 1.0  # relative effort units
    actual_effort: float = 0.0
    dependencies: list[str] = field(default_factory=list)  # IDs of goals that must complete first


class GoalHierarchy:
    """HTN-style hierarchical goal decomposition."""

    def __init__(self) -> None:
        self.goals: dict[str, GoalNode] = {}

    def add_goal(self, goal_id: str, description: str, parent_id: str | None = None, priority: int = 5) -> GoalNode:
        goal = GoalNode(id=goal_id, description=description, parent_id=parent_id, priority=priority)
        self.goals[goal_id] = goal
        if parent_id and parent_id in self.goals:
            self.goals[parent_id].children.append(goal_id)
        return goal

    def decompose(self, parent_id: str, sub_goals: list[tuple[str, str]]) -> None:
        """Break a goal into sub-goals."""
        for sub_id, sub_desc in sub_goals:
            self.add_goal(sub_id, sub_desc, parent_id=parent_id)

    def mark_completed(self, goal_id: str) -> None:
        if goal_id in self.goals:
            self.goals[goal_id].status = "completed"

    def mark_failed(self, goal_id: str) -> None:
        if goal_id in self.goals:
            self.goals[goal_id].status = "failed"

    def next_actionable(self) -> list[GoalNode]:
        """Find goals that can be acted on now (dependencies met, status=pending)."""
        actionable: list[GoalNode] = []
        for goal in self.goals.values():
            if goal.status != "pending":
                continue
            # Check dependencies.
            deps_met = all(
                self.goals.get(dep_id) and self.goals[dep_id].status == "completed"
                for dep_id in goal.dependencies
            )
            if deps_met:
                actionable.append(goal)
        actionable.sort(key=lambda g: -g.priority)
        return actionable

    def progress(self) -> dict[str, int]:
        """Return progress summary."""
        counts = {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0}
        for goal in self.goals.values():
            counts[goal.status] = counts.get(goal.status, 0) + 1
        return counts

    def tree(self, root_id: str | None = None, prefix: str = "") -> str:
        """Render the goal tree."""
        if root_id is None:
            roots = [g for g in self.goals.values() if g.parent_id is None]
        else:
            roots = [self.goals[root_id]] if root_id in self.goals else []
        lines: list[str] = []
        for root in roots:
            icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅", "failed": "❌"}[root.status]
            lines.append(f"{prefix}{icon} {root.id}: {root.description}")
            for child_id in root.children:
                lines.append(self.tree(child_id, prefix + "  "))
        return "\n".join(lines)

    def dashboard(self) -> str:
        prog = self.progress()
        lines = [
            f"Goal hierarchy ({len(self.goals)} goals):",
            f"  ✅ completed: {prog.get('completed', 0)}",
            f"  🔄 in progress: {prog.get('in_progress', 0)}",
            f"  ⬜ pending: {prog.get('pending', 0)}",
            f"  ❌ failed: {prog.get('failed', 0)}",
            "",
            "Tree:",
        ]
        lines.append(self.tree())
        return "\n".join(lines)


# ============================================================================
# EPISODIC REPLAY (offline learning from past experiences)
# ============================================================================

@dataclass
class Episode:
    """A past experience: situation, action, outcome."""
    id: str
    timestamp: float
    situation: str  # what was the context
    action: str  # what the agent did
    outcome: str  # what happened
    success: bool
    lesson: str = ""  # what was learned
    replay_count: int = 0


class EpisodicMemory:
    """Stores and replays past episodes for offline learning."""

    def __init__(self) -> None:
        self.episodes: list[Episode] = []

    def record(self, situation: str, action: str, outcome: str, success: bool, lesson: str = "") -> Episode:
        ep = Episode(
            id=f"ep{len(self.episodes) + 1}",
            timestamp=time.time(),
            situation=situation, action=action, outcome=outcome,
            success=success, lesson=lesson,
        )
        self.episodes.append(ep)
        # Keep bounded.
        if len(self.episodes) > 1000:
            self.episodes = self.episodes[-500:]
        return ep

    def replay(self, num_episodes: int = 20) -> list[str]:
        """Replay past episodes to extract lessons (offline learning).

        Returns a list of learned lessons.
        """
        lessons: list[str] = []
        for ep in self.episodes[-num_episodes:]:
            ep.replay_count += 1
            if ep.lesson:
                lessons.append(ep.lesson)
            elif ep.success:
                lessons.append(f"When {ep.situation[:40]}, do {ep.action[:40]} (succeeded)")
            else:
                lessons.append(f"Avoid {ep.action[:40]} when {ep.situation[:40]} (failed)")
        return lessons

    def find_similar(self, situation: str, top_k: int = 5) -> list[Episode]:
        """Find past episodes similar to the current situation."""
        scored: list[tuple[float, Episode]] = []
        sit_lower = situation.lower()
        for ep in self.episodes:
            # Simple overlap score.
            ep_words = set(ep.situation.lower().split())
            sit_words = set(sit_lower.split())
            overlap = len(ep_words & sit_words) / max(1, len(sit_words))
            scored.append((overlap, ep))
        scored.sort(key=lambda x: -x[0])
        return [ep for _, ep in scored[:top_k] if _ > 0]

    def success_rate(self, action_pattern: str = "") -> float:
        """Compute success rate for episodes matching an action pattern."""
        matching = [e for e in self.episodes if action_pattern.lower() in e.action.lower()] if action_pattern else self.episodes
        if not matching:
            return 0.0
        return sum(1 for e in matching if e.success) / len(matching)

    def dashboard(self) -> str:
        if not self.episodes:
            return "Episodic memory: empty"
        success_count = sum(1 for e in self.episodes if e.success)
        lines = [
            f"Episodic memory ({len(self.episodes)} episodes):",
            f"  success rate: {success_count / len(self.episodes):.0%}",
            f"  replays: {sum(e.replay_count for e in self.episodes)}",
            "",
            "Recent episodes:",
        ]
        for ep in self.episodes[-10:]:
            icon = "✓" if ep.success else "✗"
            lines.append(f"  {icon} [{ep.timestamp:.0f}] {ep.action[:50]} → {ep.outcome[:40]}")
        return "\n".join(lines)


# ============================================================================
# UNIFIED COGNITIVE ARCHITECTURE
# ============================================================================

class CognitiveArchitecture:
    """Combines all memory systems into a unified cognitive architecture."""

    def __init__(self) -> None:
        self.procedural = ProceduralMemory()
        self.declarative = DeclarativeMemory()
        self.working = WorkingMemory(capacity=7)
        self.goals = GoalHierarchy()
        self.episodic = EpisodicMemory()

    def think(self, situation: str, current_state: dict[str, Any]) -> dict[str, Any]:
        """Process a situation through all memory systems.

        Returns suggested actions, recalled facts, and working memory state.
        """
        # 1. Check procedural memory for matching rules.
        actions = self.procedural.suggest_actions(current_state)
        # 2. Recall relevant facts from declarative memory.
        facts = self.declarative.recall(situation, top_k=3)
        # 3. Add situation to working memory.
        self.working.add("current_situation", situation, relevance=1.0)
        # 4. Find similar past episodes.
        similar_episodes = self.episodic.find_similar(situation, top_k=3)
        return {
            "suggested_actions": actions,
            "recalled_facts": [(score, chunk.content) for score, chunk in facts],
            "working_memory": [item.content for item in self.working.get_all()],
            "similar_episodes": [(ep.action, ep.outcome, ep.success) for ep in similar_episodes],
        }

    def learn(self, situation: str, action: str, outcome: str, success: bool, lesson: str = "") -> None:
        """Learn from an experience (record to episodic + update declarative)."""
        self.episodic.record(situation, action, outcome, success, lesson)
        if lesson:
            self.declarative.add(f"fact_{len(self.declarative.chunks)}", lesson, category="learned")

    def dashboard(self) -> str:
        lines = [
            "╔═══════════════════════════════════════════════════════════╗",
            "║              🧠  COGNITIVE ARCHITECTURE                    ║",
            "╠═══════════════════════════════════════════════════════════╣",
            f"║  Procedural rules:     {len(self.procedural.rules):<37}║",
            f"║  Declarative chunks:   {len(self.declarative.chunks):<37}║",
            f"║  Working memory:       {len(self.working.items)}/{self.working.capacity:<34}║",
            f"║  Goals:                {len(self.goals.goals):<37}║",
            f"║  Episodes:             {len(self.episodic.episodes):<37}║",
            "╚═══════════════════════════════════════════════════════════╝",
        ]
        return "\n".join(lines)


_cognitive: CognitiveArchitecture | None = None


def get_cognitive_architecture() -> CognitiveArchitecture:
    global _cognitive
    if _cognitive is None:
        _cognitive = CognitiveArchitecture()
    return _cognitive
