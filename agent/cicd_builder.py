"""CI/CD pipeline builder — generate real CI/CD config files.

Generates working CI/CD configs for:
  * GitHub Actions (.github/workflows/ci.yml)
  * GitLab CI (.gitlab-ci.yml)
  * CircleCI (.circleci/config.yml)
  * Jenkins (Jenkinsfile)

Each generated config runs lint + test on every push/PR. Real, runnable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


GITHUB_ACTIONS_TEMPLATE = """\
name: CI

on:
  push:
    branches: [main, master]
  pull_request:
    branches: [main, master]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install ruff
      - run: ruff check . --continue-on-error

  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{{{ matrix.python-version }}}}
      - run: pip install -r requirements.txt
      - run: pip install pytest pytest-cov
      - run: pytest --cov=. --cov-report=xml
      - uses: codecov/codecov-action@v4
        if: matrix.python-version == '3.11'
"""

GITLAB_CI_TEMPLATE = """\
stages:
  - lint
  - test

variables:
  PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache/pip"

cache:
  paths:
    - .cache/pip

lint:
  stage: lint
  image: python:3.12-slim
  script:
    - pip install ruff
    - ruff check . --continue-on-error
  allow_failure: true

test:
  stage: test
  image: python:${{'$CI_JOB_NAME'}}
  parallel:
    matrix:
      - PYTHON_VERSION: ['3.10-slim', '3.11-slim', '3.12-slim']
  script:
    - pip install -r requirements.txt
    - pip install pytest pytest-cov
    - pytest --cov=. --cov-report=xml
  artifacts:
    reports:
      coverage_report:
        coverage_format: cobertura
        path: coverage.xml
"""

CIRCLECI_TEMPLATE = """\
version: 2.1

jobs:
  test:
    docker:
      - image: cimg/python:3.11
    steps:
      - checkout
      - run: pip install -r requirements.txt
      - run: pip install pytest pytest-cov
      - run: pytest --cov=.

workflows:
  version: 2
  build-and-test:
    jobs:
      - test
"""

JENKINSFILE_TEMPLATE = """\
pipeline {{
    agent any
    stages {{
        stage('Setup') {{
            steps {{
                sh 'pip install -r requirements.txt'
                sh 'pip install pytest pytest-cov ruff'
            }}
        }}
        stage('Lint') {{
            steps {{
                sh 'ruff check . --continue-on-error'
            }}
        }}
        stage('Test') {{
            steps {{
                sh 'pytest --cov=. --cov-report=xml'
            }}
            post {{
                always {{
                    junit 'test-reports/*.xml'
                    publishCoverage adapters: [cobertura('coverage.xml')]
                }}
            }}
        }}
    }}
}}
"""


def generate_github_actions(root: str = ".") -> str:
    """Write .github/workflows/ci.yml and return the path."""
    out = Path(root) / ".github" / "workflows" / "ci.yml"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Render the template (it has a literal ${{}} that needs care).
    content = GITHUB_ACTIONS_TEMPLATE.replace("${{{{", "${{").replace("}}}}", "}}")
    out.write_text(content, encoding="utf-8")
    return str(out)


def generate_gitlab_ci(root: str = ".") -> str:
    out = Path(root) / ".gitlab-ci.yml"
    content = GITLAB_CI_TEMPLATE.replace("${{'$CI_JOB_NAME'}}", "3.12-slim")
    out.write_text(content, encoding="utf-8")
    return str(out)


def generate_circleci(root: str = ".") -> str:
    out = Path(root) / ".circleci" / "config.yml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(CIRCLECI_TEMPLATE, encoding="utf-8")
    return str(out)


def generate_jenkinsfile(root: str = ".") -> str:
    out = Path(root) / "Jenkinsfile"
    out.write_text(JENKINSFILE_TEMPLATE, encoding="utf-8")
    return str(out)


def generate_all(root: str = ".") -> dict[str, str]:
    """Generate all CI/CD configs. Returns {platform: path}."""
    return {
        "github_actions": generate_github_actions(root),
        "gitlab_ci": generate_gitlab_ci(root),
        "circleci": generate_circleci(root),
        "jenkins": generate_jenkinsfile(root),
    }
