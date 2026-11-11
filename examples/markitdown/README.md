# markitdown-mcp migration pilot

Pilot 4 in spec section 15.1: migrating a widely used open-source server, [markitdown-mcp](https://github.com/microsoft/markitdown) (Microsoft, MIT), to OunceMCP. It has one tool, `convert_to_markdown(uri)`, which returns a whole document as Markdown for any URI.

| File | Step |
| --- | --- |
| `original.py` | The published server's tool, re-created on FastMCP with the same name, description and behavior |
| `step1.py` | Step 1: the original wrapped by `OunceServer.wrap`, no tool changes |
| `server.py` | Migrated: typed output, preview plus handle, URL and file checks |
| `converter.py` | markitdown's converter, loaded on first use |

```bash
pip install 'markitdown[all]'
ouncemcp audit examples/markitdown/original.py:mcp
ouncemcp check examples/markitdown/server.py
python examples/markitdown/server.py
```

Results, from `tests/test_pilot_markitdown.py` with a fake converter returning a 60-section report:

| | Original | Migrated |
| --- | ---: | ---: |
| Result tokens | 14,760 | 388 (then 547 per 2-chunk page) |
| Metadata, loopback and private URLs | Fetched | Refused (`url_not_allowed`) |
| `file:` URIs | Any path | Refused unless `MARKITDOWN_ALLOW_FILES=1`, then only under `MARKITDOWN_ROOT` |
| Tool definition tokens | 85 | 394 (260 of them are `handles.read` and `jobs.get`) |

The step-by-step guide is `docs/guides/migrating-from-fastmcp.md`. Not covered: the upstream server's plugin option and its HTTP and SSE transports, which OunceMCP's own transports replace.
