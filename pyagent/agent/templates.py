"""Prompt templates — customizable system prompts and templates.

Inspired by gsd-pi's prompt-templates module, this module provides:
  - Load and manage prompt templates
  - Variable substitution
  - Template inheritance
  - Custom prompt creation
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PromptTemplate:
    """A prompt template with variables."""
    name: str
    content: str
    description: str = ""
    variables: list[str] = field(default_factory=list)
    parent: str | None = None  # Template to inherit from
    tags: list[str] = field(default_factory=list)
    
    def render(self, **kwargs: Any) -> str:
        """Render the template with variable substitution.
        
        Args:
            **kwargs: Variable values.
        
        Returns:
            Rendered template string.
        """
        result = self.content
        
        # Replace {{variable}} patterns
        for key, value in kwargs.items():
            pattern = r"\{\{" + re.escape(key) + r"\}\}"
            result = re.sub(pattern, str(value), result)
        
        # Remove unresolved variables
        result = re.sub(r"\{\{\w+\}\}", "", result)
        
        return result.strip()
    
    def extract_variables(self) -> list[str]:
        """Extract variable names from the template."""
        return list(set(re.findall(r"\{\{(\w+)\}\}", self.content)))


# Built-in templates
BUILTIN_TEMPLATES: dict[str, PromptTemplate] = {
    "code_review": PromptTemplate(
        name="code_review",
        content="""Review the following code for bugs, security issues, and style problems.

Code to review:
```{{language}}
{{code}}
```

Please provide:
1. Security vulnerabilities
2. Bug risks
3. Performance issues
4. Style improvements
5. Overall assessment""",
        description="Template for code review requests",
        variables=["language", "code"],
        tags=["code", "review"],
    ),
    
    "debug_error": PromptTemplate(
        name="debug_error",
        content="""Debug the following error:

Error: {{error_message}}

Context:
- File: {{file_path}}
- Function: {{function_name}}
- Line: {{line_number}}

Please:
1. Identify the root cause
2. Explain why it happened
3. Provide a fix
4. Suggest preventive measures""",
        description="Template for debugging errors",
        variables=["error_message", "file_path", "function_name", "line_number"],
        tags=["debug", "error"],
    ),
    
    "explain_code": PromptTemplate(
        name="explain_code",
        content="""Explain the following code:

```{{language}}
{{code}}
```

Please provide:
1. What the code does
2. How it works step-by-step
3. Key concepts used
4. Potential improvements""",
        description="Template for code explanation",
        variables=["language", "code"],
        tags=["explain", "code"],
    ),
    
    "write_tests": PromptTemplate(
        name="write_tests",
        content="""Write tests for the following code:

```{{language}}
{{code}}
```

Requirements:
- Cover edge cases
- Test error handling
- Use {{test_framework}}
- Include assertions

Provide:
1. Unit tests
2. Integration tests (if applicable)
3. Test data/fixtures""",
        description="Template for writing tests",
        variables=["language", "code", "test_framework"],
        tags=["test", "code"],
    ),
    
    "refactor_code": PromptTemplate(
        name="refactor_code",
        content="""Refactor the following code for better quality:

```{{language}}
{{code}}
```

Goals:
- Improve readability
- Reduce complexity
- Follow best practices
- Maintain functionality

Provide the refactored code with explanations.""",
        description="Template for refactoring code",
        variables=["language", "code"],
        tags=["refactor", "code"],
    ),
    
    "document_code": PromptTemplate(
        name="document_code",
        content="""Add documentation to the following code:

```{{language}}
{{code}}
```

Include:
1. Module/docstring
2. Function descriptions
3. Parameter documentation
4. Return value documentation
5. Usage examples""",
        description="Template for documenting code",
        variables=["language", "code"],
        tags=["docs", "code"],
    ),
}


class TemplateManager:
    """Manage prompt templates."""
    
    def __init__(self):
        self.templates: dict[str, PromptTemplate] = dict(BUILTIN_TEMPLATES)
        self.custom_dir: Path | None = None
    
    def get_template(self, name: str) -> PromptTemplate | None:
        """Get a template by name."""
        return self.templates.get(name)
    
    def list_templates(self, tag: str | None = None) -> list[PromptTemplate]:
        """List all templates, optionally filtered by tag."""
        templates = list(self.templates.values())
        
        if tag:
            templates = [t for t in templates if tag in t.tags]
        
        return sorted(templates, key=lambda t: t.name)
    
    def add_template(self, template: PromptTemplate) -> None:
        """Add or update a template."""
        self.templates[template.name] = template
    
    def remove_template(self, name: str) -> bool:
        """Remove a template."""
        if name in BUILTIN_TEMPLATES:
            return False  # Can't remove built-in templates
        
        if name in self.templates:
            del self.templates[name]
            return True
        return False
    
    def render(self, name: str, **kwargs: Any) -> str | None:
        """Render a template with variables.
        
        Args:
            name: Template name.
            **kwargs: Variable values.
        
        Returns:
            Rendered template or None if not found.
        """
        template = self.get_template(name)
        if template is None:
            return None
        
        return template.render(**kwargs)
    
    def search(self, query: str) -> list[PromptTemplate]:
        """Search templates by name or description."""
        query_lower = query.lower()
        results: list[PromptTemplate] = []
        
        for template in self.templates.values():
            if (
                query_lower in template.name.lower()
                or query_lower in template.description.lower()
                or any(query_lower in tag for tag in template.tags)
            ):
                results.append(template)
        
        return sorted(results, key=lambda t: t.name)
    
    def load_from_dir(self, directory: Path) -> int:
        """Load templates from a directory.
        
        Args:
            directory: Directory containing template files.
        
        Returns:
            Number of templates loaded.
        """
        if not directory.exists():
            return 0
        
        count = 0
        
        for path in directory.glob("*.md"):
            try:
                content = path.read_text(encoding="utf-8")
                
                # Parse frontmatter if present
                name = path.stem
                description = ""
                tags: list[str] = []
                variables: list[str] = []
                
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    if len(parts) >= 3:
                        frontmatter = parts[1]
                        content = parts[2].strip()
                        
                        for line in frontmatter.strip().split("\n"):
                            if line.startswith("name:"):
                                name = line.split(":", 1)[1].strip()
                            elif line.startswith("description:"):
                                description = line.split(":", 1)[1].strip()
                            elif line.startswith("tags:"):
                                tags_str = line.split(":", 1)[1].strip()
                                tags = [t.strip() for t in tags_str.split(",")]
                
                # Extract variables from content
                variables = list(set(re.findall(r"\{\{(\w+)\}\}", content)))
                
                template = PromptTemplate(
                    name=name,
                    content=content,
                    description=description,
                    variables=variables,
                    tags=tags,
                )
                
                self.add_template(template)
                count += 1
                
            except Exception:
                continue
        
        return count
    
    def save_to_dir(self, directory: Path) -> int:
        """Save custom templates to a directory.
        
        Args:
            directory: Directory to save templates to.
        
        Returns:
            Number of templates saved.
        """
        directory.mkdir(parents=True, exist_ok=True)
        count = 0
        
        for name, template in self.templates.items():
            if name in BUILTIN_TEMPLATES:
                continue  # Skip built-in templates
            
            try:
                # Build frontmatter
                lines = ["---"]
                lines.append(f"name: {template.name}")
                if template.description:
                    lines.append(f"description: {template.description}")
                if template.tags:
                    lines.append(f"tags: {', '.join(template.tags)}")
                lines.append("---")
                lines.append("")
                lines.append(template.content)
                
                path = directory / f"{name}.md"
                path.write_text("\n".join(lines), encoding="utf-8")
                count += 1
                
            except Exception:
                continue
        
        return count


# Global instance
_manager: TemplateManager | None = None


def get_template_manager() -> TemplateManager:
    """Get the global template manager instance."""
    global _manager
    if _manager is None:
        _manager = TemplateManager()
    return _manager
