# Appendix A: the command line

Every command takes a server as `path/to/server.py[:name]` or `package.module[:name]`. Without `:name`, the first `OunceServer` (or else FastMCP) instance in the module is used. Run `ouncemcp COMMAND --help` for the full list of options.

| Command | Use it to | Common options |
| --- | --- | --- |
| `init NAME` | Scaffold a server with tests and CI | |
| `dev SERVER` | Run with hot reload and per-call token counts | `--transport`, `--host`, `--port`, `--once` |
| `check SERVER` | Measure definitions against the budget and run lints; non-zero exit on failure | `--json`, `--tokenizer` |
| `context SERVER [TOOL]` | Print exactly what the model receives | `--call JSON`, `--html PATH` |
| `eval SUITE` | Run a task suite | `--model replay\|openrouter:ID`, `--max-cost`, `--repeats`, `--split`, `--output`, `--html`, `--compare`, `--fail-on-regression` |
| `bench FILE` | Run the benchmark across models and servers | `--models`, `--max-cost`, `--from`, `--compare`, `--html` |
| `trace FILE` | Open a JSONL trace in the viewer | `--html`, `--no-html`, `--json` |
| `audit TARGET` | Measure any MCP server (file, `-- command`, or URL) | `--sample TOOL=JSON`, `--bearer`, `--oauth`, `--compare`, `--fail-startup`, `--fail-tool`, `--html` |

## Environment variables

| Variable | Meaning |
| --- | --- |
| `OUNCEMCP_CURSOR_KEYS` | Comma-separated signing keys, newest first, shared by every replica |
| `OPENROUTER_API_KEY` | Key for `eval` and `bench` with real models |
| `OUNCEMCP_OAUTH_CLIENT_SECRET` | Client secret for `audit --oauth` (never passed as an argument) |
