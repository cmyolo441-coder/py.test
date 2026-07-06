"""Project scaffolding tools — generate real project structures from templates.

Each scaffolder writes real, working starter files to disk. The agent uses
these to bootstrap new projects as part of Goal Mode workflows.
"""
from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolResult


def scaffold_python_project(name: str, base_dir: str = ".") -> ToolResult:
    """Create a complete Python package structure with tests, CI, docs."""
    root = Path(base_dir) / name
    if root.exists():
        return ToolResult(output=f"Directory already exists: {root}", success=False)
    pkg = root / name.replace("-", "_")
    pkg.mkdir(parents=True, exist_ok=True)
    # __init__.py
    (pkg / "__init__.py").write_text('"""{name} package."""\n\n__version__ = "0.1.0"\n'.replace("{name}", name), encoding="utf-8")
    # main module
    (pkg / "main.py").write_text(
        f'"""Entry point for {name}."""\n\n\ndef main() -> None:\n'
        f'    print("Hello from {name}!")\n\n\n'
        f'if __name__ == "__main__":\n    main()\n',
        encoding="utf-8",
    )
    # tests
    tests = root / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "__init__.py").write_text("", encoding="utf-8")
    (tests / "test_main.py").write_text(
        f'from {name.replace("-","_")}.main import main\n\n\n'
        f'def test_main_runs(capsys):\n    main()\n    captured = capsys.readouterr()\n'
        f'    assert "Hello from {name}" in captured.out\n',
        encoding="utf-8",
    )
    # pyproject.toml
    (root / "pyproject.toml").write_text(
        f'[build-system]\nrequires = ["setuptools>=68", "wheel"]\n'
        f'build-backend = "setuptools.build_meta"\n\n'
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
        f'description = "TODO: describe {name}"\nrequires-python = ">=3.10"\n'
        f'license = {{text = "MIT"}}\n\n'
        f'[project.optional-dependencies]\ndev = ["pytest>=8.0", "ruff>=0.4.0"]\n\n'
        f'[project.scripts]\n{name} = "{name.replace("-","_")}.main:main"\n\n'
        f'[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
        encoding="utf-8",
    )
    # README
    (root / "README.md").write_text(
        f"# {name}\n\nTODO: describe the project.\n\n"
        f"## Install\n\n```bash\npip install -e .[dev]\n```\n\n"
        f"## Run\n\n```bash\n{name}\n```\n\n## Test\n\n```bash\npytest\n```\n",
        encoding="utf-8",
    )
    # .gitignore
    (root / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\n*.egg-info/\n.venv/\n.env\n*.log\n"
        ".pytest_cache/\nbuild/\ndist/\n.mypy_cache/\n.ruff_cache/\n",
        encoding="utf-8",
    )
    # Makefile
    (root / "Makefile").write_text(
        f".PHONY: install dev test lint run\n\n"
        f"install:\n\tpip install -e .\n\n"
        f"dev:\n\tpip install -e .[dev]\n\n"
        f"test:\n\tpython -m pytest -q\n\n"
        f"lint:\n\truff check {name.replace('-','_')} tests\n\n"
        f"run:\n\tpython -m {name.replace('-','_')}.main\n",
        encoding="utf-8",
    )
    return ToolResult(
        output=f"Created Python project '{name}' at {root}\n"
               f"  {pkg}/__init__.py, main.py\n  tests/test_main.py\n  "
               f"pyproject.toml, README.md, .gitignore, Makefile",
        metadata={"root": str(root)},
    )


def scaffold_node_project(name: str, base_dir: str = ".") -> ToolResult:
    """Create a minimal Node.js project with package.json and index.js."""
    root = Path(base_dir) / name
    if root.exists():
        return ToolResult(output=f"Directory already exists: {root}", success=False)
    root.mkdir(parents=True)
    (root / "package.json").write_text(
        f'{{\n  "name": "{name}",\n  "version": "1.0.0",\n'
        f'  "main": "index.js",\n  "scripts": {{\n'
        f'    "start": "node index.js",\n    "test": "node --test"\n  }}\n}}\n',
        encoding="utf-8",
    )
    (root / "index.js").write_text(
        f'// Entry point for {name}\n'
        f'console.log("Hello from {name}!");\n',
        encoding="utf-8",
    )
    (root / "README.md").write_text(f"# {name}\n\nTODO: describe.\n\n```bash\nnpm start\n```\n", encoding="utf-8")
    (root / ".gitignore").write_text("node_modules/\n*.log\n.env\ndist/\n", encoding="utf-8")
    return ToolResult(output=f"Created Node.js project '{name}' at {root}")


def scaffold_web_project(name: str, base_dir: str = ".") -> ToolResult:
    """Create a static HTML/CSS/JS website skeleton."""
    root = Path(base_dir) / name
    if root.exists():
        return ToolResult(output=f"Directory already exists: {root}", success=False)
    (root / "css").mkdir(parents=True)
    (root / "js").mkdir(parents=True)
    (root / "index.html").write_text(
        f'<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="UTF-8">\n'
        f'  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'  <title>{name}</title>\n  <link rel="stylesheet" href="css/style.css">\n'
        f'</head>\n<body>\n  <h1>{name}</h1>\n  <p>Welcome!</p>\n'
        f'  <script src="js/main.js"></script>\n</body>\n</html>\n',
        encoding="utf-8",
    )
    (root / "css" / "style.css").write_text(
        "body { font-family: system-ui, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; }\n",
        encoding="utf-8",
    )
    (root / "js" / "main.js").write_text(
        f'// {name} main script\nconsole.log("{name} loaded");\n',
        encoding="utf-8",
    )
    (root / "README.md").write_text(f"# {name}\n\nStatic website. Open `index.html` in a browser.\n", encoding="utf-8")
    return ToolResult(output=f"Created web project '{name}' at {root}")


def get_scaffold_tools() -> list[Tool]:
    return [
        Tool(
            name="scaffold_python_project",
            description="Scaffold a complete Python package (src, tests, CI config, README).",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}, "base_dir": {"type": "string", "default": "."}},
                "required": ["name"],
            },
            func=scaffold_python_project,
            dangerous=True,
        ),
        Tool(
            name="scaffold_node_project",
            description="Scaffold a minimal Node.js project (package.json, index.js).",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}, "base_dir": {"type": "string", "default": "."}},
                "required": ["name"],
            },
            func=scaffold_node_project,
            dangerous=True,
        ),
        Tool(
            name="scaffold_web_project",
            description="Scaffold a static HTML/CSS/JS website skeleton.",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}, "base_dir": {"type": "string", "default": "."}},
                "required": ["name"],
            },
            func=scaffold_web_project,
            dangerous=True,
        ),
    ]
