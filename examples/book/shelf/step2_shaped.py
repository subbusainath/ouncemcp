"""Shelf, step 2: results shaped for the model (the book, chapters 4 and 5).

Adds paging with signed cursors, detail-only fields, skipping work the caller did
not ask for, and errors that tell the model what to do next.
"""

from __future__ import annotations

from typing import Annotated

from library import Library
from pydantic import BaseModel, Field

from ouncemcp import (
    Cursor,
    Detail,
    Effect,
    InvalidInput,
    NotFound,
    OunceServer,
    Paginated,
    ToolContext,
)

LIBRARY = Library()

server = OunceServer(
    "shelf",
    instructions="A library catalog. Search for books, then get one book by its id.",
)


# --8<-- [start:models]
class BookSummary(BaseModel):
    id: str
    title: str
    author: str
    year: Annotated[int | None, Detail] = None  # only with response_format=detailed
    subject: Annotated[str | None, Detail] = None


class SearchResult(BaseModel):
    books: Annotated[list[BookSummary], Paginated]
    total: int
    next_cursor: Cursor = None


class BookDetail(BaseModel):
    id: str
    title: str
    author: str
    subject: str
    year: int
    branch: str
    available: int
    summary: Annotated[str | None, Detail] = None
    reviews: Annotated[list[str] | None, Detail] = None


# --8<-- [end:models]


# --8<-- [start:search]
@server.tool(name="books.search", effect=Effect.READ, deadline_ms=5_000)
def search(
    ctx: ToolContext,
    query: str = Field(description="Words from the title or the author's name"),
    limit: int = Field(10, ge=1, le=50),
    cursor: str | None = Field(None, description="next_cursor from the previous page"),
) -> SearchResult:
    """Find books whose title or author contains every word of the query.
    Use response_format=detailed for year and subject, and books.get for one book."""
    words = query.lower().split()
    if not words:
        raise InvalidInput(
            "The query is empty.", hint="Give at least one word from a title or author."
        )
    found = [
        b
        for b in LIBRARY.books.values()
        if all(w in f"{b.title} {b.author}".lower() for w in words)
    ]
    page = ctx.page(found, cursor=cursor, limit=limit)
    return SearchResult(
        books=[
            BookSummary(id=b.id, title=b.title, author=b.author, year=b.year, subject=b.subject)
            for b in page.items
        ],
        total=len(found),
        next_cursor=page.next_cursor,
    )


# --8<-- [end:search]


# --8<-- [start:get]
@server.tool(name="books.get", effect=Effect.READ, deadline_ms=5_000)
async def get(
    ctx: ToolContext,
    id: str = Field(description="Book id from books.search, for example b-007"),
) -> BookDetail:
    """Get one book: title, author, subject, year, branch and copies available.
    Use response_format=detailed for its summary and reader reviews."""
    book = LIBRARY.books.get(id)
    if book is None:
        raise NotFound(f"No book with id '{id}'.", hint="Use books.search to find ids.")
    detail = BookDetail(
        id=book.id,
        title=book.title,
        author=book.author,
        subject=book.subject,
        year=book.year,
        branch=book.branch,
        available=LIBRARY.available(book.id),
        summary=book.summary,
    )
    # Reviews come from a slow service, and concise results never return them.
    if ctx.includes("reviews"):
        detail.reviews = await LIBRARY.reviews(book.id)
    return detail


# --8<-- [end:get]


if __name__ == "__main__":
    server.run()
