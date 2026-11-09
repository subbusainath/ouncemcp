import importlib

import pytest

import ouncemcp
from ouncemcp.cli import COMMANDS, main

SUBPACKAGES = ["core", "discovery", "execution", "client", "eval", "trace", "cli", "audit"]


def test_version():
    assert ouncemcp.__version__ == "0.0.0"


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackages_import(name):
    importlib.import_module(f"ouncemcp.{name}")


def test_cli_help_exits_zero(capsys):
    assert main([]) == 0
    assert "ouncemcp" in capsys.readouterr().out


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_unimplemented_commands_report_milestone(command, capsys):
    assert main([command]) == 2
    assert "not implemented yet" in capsys.readouterr().err
