"""Tests for the CLI argument parser."""

from __future__ import annotations

from agent.cli import parse_args


def test_defaults():
    args = parse_args([])
    assert args.provider is None
    assert args.prompt is None
    assert args.no_anim is False


def test_provider_and_model():
    args = parse_args(["-p", "zen", "-m", "big-pickle"])
    assert args.provider == "zen"
    assert args.model == "big-pickle"


def test_one_shot_prompt():
    args = parse_args(["-c", "hello there"])
    assert args.prompt == "hello there"


def test_flags():
    args = parse_args(["--no-anim", "--auto", "--version"])
    assert args.no_anim is True
    assert args.auto is True
    assert args.version is True
