import json

from conftest import EXAMPLE

from ouncemcp.cli import main


def test_check_example_passes(capsys):
    assert main(["check", str(EXAMPLE / "server.py")]) == 0
    assert "inventory.search" in capsys.readouterr().out


def test_check_json_and_plain_fastmcp(capsys):
    assert main(["check", f"{EXAMPLE / 'plain_server.py'}:mcp", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {t["name"] for t in data["tools"]} == {"search_resources", "get_resource"}


def test_context_shows_definition(capsys):
    assert main(["context", str(EXAMPLE / "server.py"), "inventory.get"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["name"] == "inventory.get"
    assert "tokens" in out.err


def test_context_unknown_tool(capsys):
    assert main(["context", str(EXAMPLE / "server.py"), "nope"]) == 2
    assert "inventory.search" in capsys.readouterr().err


def test_missing_file(capsys):
    assert main(["check", "does/not/exist.py"]) == 2
