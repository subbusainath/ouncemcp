# Contributing to OunceMCP

OunceMCP is pre-alpha. The best contributions right now are feedback on the [specification](docs/spec.md) through issues.

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
