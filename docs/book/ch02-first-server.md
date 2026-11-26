# 2. A first server

In this chapter we write the smallest useful version of Shelf: search the catalog, and get one book.

## The library

The library's data lives in `library.py`: 60 books split across two branches, `north` and `south`, plus the loans and a slow "reviews" service we will meet in chapter 4. It is plain Python with no OunceMCP in it, so we will not look at it here. All you need to know is that `make_catalog()` returns a dictionary of `Book` objects keyed by id (`b-001` to `b-060`).

## Step 1: the catalog

Here is the whole server:

```python title="examples/book/shelf/step1_catalog.py"
--8<-- "examples/book/shelf/step1_catalog.py"
```

Let's go through it.

### The server

```python
server = OunceServer("shelf", instructions="A library catalog. ...")
```

`OunceServer` wraps a FastMCP server. The name identifies it to hosts; the `instructions` are a sentence or two the host may show the model about the server as a whole. Keep them short: they are context too.

### Tools have names, effects and types

```python
@server.tool(name="books.search", effect=Effect.READ)
def search(query: str = Field(description=...), limit: int = Field(5, ge=1, le=10)) -> SearchResult:
```

Three things are required on every tool, and OunceMCP refuses to start without them:

1. **A name.** Use `noun.verb` names (`books.search`, `books.get`) so related tools sort together and the model can tell them apart.
2. **An effect.** `Effect.READ` says the tool changes nothing. Writes are `Effect.WRITE` or `Effect.DESTRUCTIVE` (chapter 6). The effect decides retries, approvals and lints, so it is never guessed.
3. **A typed result.** The return annotation must be a Pydantic model. That model becomes the tool's output schema, and OunceMCP uses it to shape results that are too large without ever breaking the schema.

!!! note
    Try removing the return annotation. The server fails at import with *"Tool 'books.search' must be annotated to return a Pydantic model"*. Mistakes like this surface when you write them, not when a model trips over them.

### Descriptions are for the model

The docstring is the tool's description, and each `Field(description=...)` documents a parameter. The model reads these to decide which tool to call and what to pass, so write them for it:

- Say what the tool does **and what it returns**.
- Say where argument values come from ("Book id from books.search").
- Point to the next tool ("Use books.get for the details of one book").

Bounds matter as much as words. `limit: int = Field(5, ge=1, le=10)` stops the model asking for 10,000 rows, and `Field(max_length=10)` on the result list says the result can never hold more.

### Errors are values the model can act on

```python
raise NotFound(f"No book with id '{id}'.", hint="Use books.search to find ids.")
```

When a book does not exist we raise `NotFound` with a hint. The model receives the message, the hint and a machine-readable category. Chapter 5 covers errors in full.

## Running it

Run the server with hot reload:

```bash
ouncemcp dev examples/book/shelf/step1_catalog.py
```

`dev` serves on `http://127.0.0.1:8000/mcp`, restarts when you save, and prints one line per call with the tokens the result used. Point any MCP host or the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) at that URL and call `books.search` with `{"query": "ada"}`.

To use it from Claude Desktop over stdio instead, add it to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "shelf": {"command": "uv", "args": ["run", "--directory", "/path/to/ouncemcp", "python", "examples/book/shelf/step1_catalog.py"]}
  }
}
```

## Seeing what the model sees

`ouncemcp context` prints the exact definition a client receives:

```bash
ouncemcp context examples/book/shelf/step1_catalog.py books.get
```

```json
{
  "name": "books.get",
  "description": "Get one book: title, author, subject, year and branch. Use books.search to find ids.",
  "inputSchema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "id": {"description": "Book id from books.search, for example b-007", "type": "string"}
    },
    "required": ["id"]
  },
  "outputSchema": { ... }
}
```

Every one of those characters is in the model's context for the whole session. The next chapter puts a number on it.

## Summary

- A tool needs a `noun.verb` name, an `Effect` and a Pydantic return type.
- Descriptions and field descriptions are written for the model: what, returns, where values come from, what to call next.
- Bound every input and every list.
- `ouncemcp dev` runs the server; `ouncemcp context` shows what the model receives.

!!! question "Questions about this chapter?"
    Ask in `#help` on the [OunceMCP Discord](https://discord.gg/mQz4ZuZe3u), one post per question.
