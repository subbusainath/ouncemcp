import importlib

import pytest

import ouncemcp
from ouncemcp.cli import COMMANDS, IMPLEMENTED, main

SUBPACKAGES = ["core", "discovery", "execution", "client", "eval", "trace", "cli", "audit"]


def test_version():
    assert ouncemcp.__version__ == "1.0.0rc2"


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackages_import(name):
    importlib.import_module(f"ouncemcp.{name}")


def test_cli_help_exits_zero(capsys):
    assert main([]) == 0
    assert "ouncemcp" in capsys.readouterr().out


@pytest.mark.parametrize("command", sorted(set(COMMANDS) - IMPLEMENTED))
def test_unimplemented_commands_report_milestone(command, capsys):
    assert main([command]) == 2
    assert "not implemented yet" in capsys.readouterr().err


def test_readme_links_work_on_pypi():
    """PyPI shows README.md as the project description, where relative links 404."""
    import re
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    targets = re.findall(r"\]\(([^)\s]+)\)", readme)
    relative = [t for t in targets if not re.match(r"(https?:|#|mailto:)", t)]
    assert not relative, f"use absolute URLs in README.md (shown on PyPI): {relative}"
