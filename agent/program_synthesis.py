"""Program synthesis — generate programs from specifications.

The agent can generate code from:
  - Natural language specifications
  - Input/output examples (inductive synthesis)
  - Formal specifications (preconditions + postconditions)
  - Type signatures

Uses a combination of:
  - LLM-based generation (primary)
  - Template-based synthesis (fallback)
  - Example-driven refinement (inductive)

Inspired by: FlashFill, SketCode, DeepCoder.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SynthesisSpec:
    """A program specification."""
    description: str = ""  # natural language
    inputs: list[Any] = field(default_factory=list)  # example inputs
    outputs: list[Any] = field(default_factory=list)  # expected outputs
    input_type: str = ""  # e.g., "list[int]"
    output_type: str = ""
    constraints: list[str] = field(default_factory=list)  # e.g., "O(n) time"
    function_name: str = "synthesized"


@dataclass
class SynthesisResult:
    spec: SynthesisSpec
    code: str
    language: str = "python"
    verified: bool = False  # did it pass the examples?
    test_results: list[tuple[Any, Any, bool]] = field(default_factory=list)  # (input, expected, passed)
    alternatives: list[str] = field(default_factory=list)


class ProgramSynthesizer:
    """Generates programs from specifications."""

    # Template library for common patterns.
    TEMPLATES: dict[str, str] = {
        "identity": "def {name}(x):\n    return x\n",
        "square": "def {name}(x):\n    return x * x\n",
        "double": "def {name}(x):\n    return x * 2\n",
        "length": "def {name}(lst):\n    return len(lst)\n",
        "sum_list": "def {name}(lst):\n    return sum(lst)\n",
        "max_list": "def {name}(lst):\n    return max(lst) if lst else None\n",
        "sort_list": "def {name}(lst):\n    return sorted(lst)\n",
        "reverse": "def {name}(lst):\n    return lst[::-1]\n",
        "filter_positive": "def {name}(lst):\n    return [x for x in lst if x > 0]\n",
        "count_occurrences": "def {name}(lst, item):\n    return lst.count(item)\n",
    }

    def synthesize_from_examples(self, spec: SynthesisSpec) -> SynthesisResult:
        """Synthesize a program from input/output examples (inductive synthesis)."""
        if not spec.inputs or not spec.outputs:
            return SynthesisResult(spec=spec, code="# Cannot synthesize without examples", verified=False)

        # Try each template.
        for template_name, template_code in self.TEMPLATES.items():
            code = template_code.format(name=spec.function_name)
            if self._verify_against_examples(code, spec):
                return SynthesisResult(
                    spec=spec, code=code, verified=True,
                    test_results=self._get_test_results(code, spec),
                )

        # If no template matches, try to infer a simple transformation.
        inferred = self._infer_transformation(spec)
        if inferred:
            return SynthesisResult(
                spec=spec, code=inferred, verified=self._verify_against_examples(inferred, spec),
                test_results=self._get_test_results(inferred, spec),
            )

        # Fallback: generate a generic function that memorizes the mapping.
        fallback = self._generate_memorization(spec)
        return SynthesisResult(spec=spec, code=fallback, verified=False)

    def _verify_against_examples(self, code: str, spec: SynthesisSpec) -> bool:
        """Check if the code produces the expected outputs for all examples."""
        try:
            # Parse and execute the code.
            tree = ast.parse(code)
            namespace: dict[str, Any] = {}
            exec(compile(tree, "<synthesis>", "exec"), namespace)
            func = namespace.get(spec.function_name)
            if func is None:
                return False
            for inp, expected in zip(spec.inputs, spec.outputs):
                inp_args = inp if isinstance(inp, (list, tuple)) else (inp,)
                actual = func(*inp_args)
                if actual != expected:
                    return False
            return True
        except Exception:  # noqa: BLE001
            return False

    def _get_test_results(self, code: str, spec: SynthesisSpec) -> list[tuple[Any, Any, bool]]:
        results: list[tuple[Any, Any, bool]] = []
        try:
            namespace: dict[str, Any] = {}
            exec(compile(ast.parse(code), "<synthesis>", "exec"), namespace)
            func = namespace.get(spec.function_name)
            if func is None:
                return results
            for inp, expected in zip(spec.inputs, spec.outputs):
                inp_args = inp if isinstance(inp, (list, tuple)) else (inp,)
                actual = func(*inp_args)
                results.append((inp, expected, actual == expected))
        except Exception:  # noqa: BLE001
            pass
        return results

    def _infer_transformation(self, spec: SynthesisSpec) -> str:
        """Try to infer a simple transformation from examples."""
        if len(spec.inputs) < 2:
            return ""
        # Check for arithmetic operations.
        for inp, out in zip(spec.inputs, spec.outputs):
            if isinstance(inp, (int, float)) and isinstance(out, (int, float)):
                # Check if it's multiplication.
                if inp != 0:
                    ratio = out / inp
                    if ratio == int(ratio):
                        return f"def {spec.function_name}(x):\n    return x * {int(ratio)}\n"
                # Check if it's addition.
                diff = out - inp
                if all((o - i) == diff for i, o in zip(spec.inputs, spec.outputs) if isinstance(i, (int, float)) and isinstance(o, (int, float))):
                    return f"def {spec.function_name}(x):\n    return x + {diff}\n"
        # Check for list operations.
        if all(isinstance(i, list) for i in spec.inputs):
            for inp, out in zip(spec.inputs, spec.outputs):
                if out == len(inp):
                    return f"def {spec.function_name}(lst):\n    return len(lst)\n"
                if out == sorted(inp):
                    return f"def {spec.function_name}(lst):\n    return sorted(lst)\n"
                if out == inp[::-1]:
                    return f"def {spec.function_name}(lst):\n    return lst[::-1]\n"
                if out == sum(inp) and isinstance(out, (int, float)):
                    return f"def {spec.function_name}(lst):\n    return sum(lst)\n"
        return ""

    def _generate_memorization(self, spec: SynthesisSpec) -> str:
        """Generate a function that memorizes the input-output mapping (fallback)."""
        lines = [f"def {spec.function_name}(x):"]
        lines.append(f"    # Memorized mapping (fallback — no pattern inferred)")
        lines.append(f"    mapping = {{")
        for inp, out in zip(spec.inputs, spec.outputs):
            lines.append(f"        {repr(inp)}: {repr(out)},")
        lines.append(f"    }}")
        lines.append(f"    return mapping.get(x, None)")
        return "\n".join(lines)

    def synthesize_from_description(self, description: str, function_name: str = "synthesized") -> SynthesisResult:
        """Synthesize from natural language (template-based heuristic)."""
        spec = SynthesisSpec(description=description, function_name=function_name)
        desc_lower = description.lower()
        # Match common patterns.
        if "square" in desc_lower:
            code = self.TEMPLATES["square"].format(name=function_name)
        elif "double" in desc_lower:
            code = self.TEMPLATES["double"].format(name=function_name)
        elif "sort" in desc_lower:
            code = self.TEMPLATES["sort_list"].format(name=function_name)
        elif "reverse" in desc_lower:
            code = self.TEMPLATES["reverse"].format(name=function_name)
        elif "sum" in desc_lower and "list" in desc_lower:
            code = self.TEMPLATES["sum_list"].format(name=function_name)
        elif "length" in desc_lower or "count" in desc_lower:
            code = self.TEMPLATES["length"].format(name=function_name)
        elif "maximum" in desc_lower or "max" in desc_lower:
            code = self.TEMPLATES["max_list"].format(name=function_name)
        else:
            code = f'def {function_name}(x):\n    """TODO: implement — {description}"""\n    pass\n'
        return SynthesisResult(spec=spec, code=code, verified=False)

    def list_templates(self) -> list[str]:
        return list(self.TEMPLATES.keys())


_synthesizer: ProgramSynthesizer | None = None


def get_synthesizer() -> ProgramSynthesizer:
    global _synthesizer
    if _synthesizer is None:
        _synthesizer = ProgramSynthesizer()
    return _synthesizer
