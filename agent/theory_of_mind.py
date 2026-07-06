"""Theory of mind — agent models what other agents/users think.

The agent maintains mental models of users and other agents:
  - Skill level (beginner, intermediate, expert)
  - Preferences (terse vs verbose, examples vs explanation)
  - Knowledge (what they already know)
  - Goals (what they're trying to achieve)
  - Emotional state (frustrated, curious, confident)

These models are updated based on interactions and used to tailor responses.

Example:
  - User asks the same question 3 times → frustration detected → give clearer answer
  - User uses advanced terminology → expert → skip basics
  - User makes beginner mistakes → beginner → add explanations
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class MentalModel:
    """Agent's model of a user/agent."""
    entity_id: str  # user id or agent name
    skill_level: str = "intermediate"  # beginner | intermediate | expert
    preferences: dict[str, str] = field(default_factory=dict)  # e.g., {"verbosity": "terse"}
    knowledge: dict[str, bool] = field(default_factory=dict)  # topic -> knows
    goals: list[str] = field(default_factory=list)
    emotional_state: str = "neutral"  # neutral | frustrated | curious | confident | confused
    interaction_count: int = 0
    last_interaction: float = 0.0
    repeated_questions: int = 0
    mistakes_made: int = 0
    notes: list[str] = field(default_factory=list)


class TheoryOfMind:
    """Maintains mental models of users and other agents."""

    def __init__(self) -> None:
        self.models: dict[str, MentalModel] = {}

    def get_model(self, entity_id: str = "default_user") -> MentalModel:
        if entity_id not in self.models:
            self.models[entity_id] = MentalModel(entity_id=entity_id)
        return self.models[entity_id]

    def observe_interaction(self, entity_id: str, user_input: str, agent_response: str, success: bool = True) -> None:
        """Update the mental model based on an interaction."""
        model = self.get_model(entity_id)
        model.interaction_count += 1
        model.last_interaction = time.time()

        # Detect repeated questions (frustration signal).
        if self._is_similar_to_previous(user_input, entity_id):
            model.repeated_questions += 1
            if model.repeated_questions >= 2:
                model.emotional_state = "frustrated"
                model.notes.append(f"User repeated a question {model.repeated_questions}x — may be frustrated")

        # Detect skill level from input.
        skill_signals = {
            "beginner": ["how do i", "what is", "i'm new", "help me", "explain", "i don't understand"],
            "expert": ["refactor", "optimize", "architecture", "asynchronous", "concurrency", "idempotent"],
        }
        input_lower = user_input.lower()
        for level, signals in skill_signals.items():
            if any(s in input_lower for s in signals):
                self._adjust_skill(model, level)
                break

        # Detect knowledge topics.
        topics = ["python", "javascript", "docker", "kubernetes", "sql", "git", "api", "testing"]
        for topic in topics:
            if topic in input_lower:
                model.knowledge[topic] = True

        # Detect frustration markers.
        frustration_markers = ["ugh", "not working", "still broken", "why doesn't", "stupid"]
        if any(m in input_lower for m in frustration_markers):
            model.emotional_state = "frustrated"

        # Detect curiosity.
        if "?" in user_input and model.emotional_state != "frustrated":
            model.emotional_state = "curious"

        # If interaction was successful, user might be confident.
        if success and model.interaction_count > 5 and model.emotional_state != "frustrated":
            model.emotional_state = "confident"

        # Detect mistakes (if the user mentions errors).
        if "error" in input_lower or "bug" in input_lower or "fail" in input_lower:
            model.mistakes_made += 1

    def _adjust_skill(self, model: MentalModel, new_level: str) -> None:
        """Adjust skill level, trending toward expert over time."""
        levels = ["beginner", "intermediate", "expert"]
        current_idx = levels.index(model.skill_level) if model.skill_level in levels else 1
        new_idx = levels.index(new_level)
        # Don't downgrade easily — need multiple signals.
        if new_idx > current_idx:
            model.skill_level = new_level
        elif new_idx < current_idx and model.interaction_count < 3:
            model.skill_level = new_level

    def _is_similar_to_previous(self, input: str, entity_id: str) -> bool:
        """Check if this input is similar to the previous one."""
        model = self.get_model(entity_id)
        if not model.notes:
            return False
        # Simple check: same first 20 chars.
        for note in model.notes[-3:]:
            if input[:20] in note:
                return True
        return False

    def recommend_response_style(self, entity_id: str = "default_user") -> dict[str, str]:
        """Recommend how to respond based on the user's mental model."""
        model = self.get_model(entity_id)
        style: dict[str, str] = {}

        # Verbosity based on skill.
        if model.skill_level == "beginner":
            style["verbosity"] = "verbose"
            style["include_examples"] = "yes"
            style["explain_jargon"] = "yes"
        elif model.skill_level == "expert":
            style["verbosity"] = "terse"
            style["include_examples"] = "only_if_complex"
            style["explain_jargon"] = "no"
        else:
            style["verbosity"] = "moderate"
            style["include_examples"] = "yes"
            style["explain_jargon"] = "briefly"

        # Tone based on emotional state.
        if model.emotional_state == "frustrated":
            style["tone"] = "patient"
            style["apologize_for_confusion"] = "yes"
            style["be_extra_clear"] = "yes"
        elif model.emotional_state == "curious":
            style["tone"] = "encouraging"
            style["add_extra_context"] = "yes"
        elif model.emotional_state == "confident":
            style["tone"] = "peer-to-peer"
        else:
            style["tone"] = "professional"

        return style

    def dashboard(self) -> str:
        lines = ["Theory of mind — user models:"]
        for model in self.models.values():
            lines.append(
                f"  {model.entity_id}: skill={model.skill_level}, "
                f"emotion={model.emotional_state}, interactions={model.interaction_count}, "
                f"repeated_q={model.repeated_questions}, mistakes={model.mistakes_made}"
            )
        return "\n".join(lines)


_tom: TheoryOfMind | None = None


def get_theory_of_mind() -> TheoryOfMind:
    global _tom
    if _tom is None:
        _tom = TheoryOfMind()
    return _tom
