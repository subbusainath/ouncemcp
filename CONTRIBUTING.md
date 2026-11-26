# Contributing to OunceMCP

OunceMCP is pre-alpha. The best contributions right now are feedback on the [specification](docs/spec.md) through issues.

To discuss an idea before opening an issue or a pull request, join `#contributing` on the [OunceMCP Discord](https://discord.gg/mQz4ZuZe3u).

## Good first contributions

These are designed to be small and self-contained:

- New lint rules for tool definitions, each with a fixture that triggers it and a documentation page.
- New benchmark servers or task suites with verifiable outcomes.
- Documentation fixes.

## Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest
uv run ruff check .
```

## Pull requests

- Keep changes focused and include tests.
- Changes to the public API or default behavior need a design proposal in `docs/proposals/`.
- Any change that affects token usage should state its measured effect.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Public API changes

`tests/test_api_surface.py` records every exported name and public signature of `ouncemcp`, `ouncemcp.security`, `ouncemcp.client`, `ouncemcp.eval.agent`, `ouncemcp.testing` and `ouncemcp.ui` in `tests/api_snapshot.json`. A change to the public API fails it on purpose. If the change is intended, regenerate the snapshot with `OUNCEMCP_UPDATE_API_SNAPSHOT=1 uv run pytest tests/test_api_surface.py` and explain the change in the PR. After 1.0, removing or changing a public name needs a deprecation first.

