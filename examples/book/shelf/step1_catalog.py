"""Shelf, step 1: a read-only catalog (the book, chapter 2).

Two tools: search the catalog, and get one book. Run it with
``ouncemcp dev examples/book/shelf/step1_catalog.py``.
"""

from __future__ import annotations

from library import make_catalog
from pydantic import BaseModel, Field

from ouncemcp import Effect, NotFound, OunceServer

CATALOG = make_catalog()

server = OunceServer(
    "shelf",
    instructions="A library catalog. Search for books, then get one book by its id.",
)


class BookSummary(BaseModel):
    id: str
    title: str
    author: str


class SearchResult(BaseModel):
    books: list[BookSummary] = Field(max_length=10)
    total: int


class BookDetail(BaseModel):
    id: str
    title: str
    author: str
    subject: str
    year: int
    branch: str


@server.tool(name="books.search", effect=Effect.READ)
def search(
    query: str = Field(description="Words from the title or the author's name"),
    limit: int = Field(5, ge=1, le=10),
) -> SearchResult:
    """Find books whose title or author contains every word of the query.
    Use books.get for the details of one book."""
    words = query.lower().split()
    found = [
        b for b in CATALOG.values() if all(w in f"{b.title} {b.author}".lower() for w in words)
    ]
    return SearchResult(
        books=[BookSummary(id=b.id, title=b.title, author=b.author) for b in found[:limit]],
        total=len(found),
    )


@server.tool(name="books.get", effect=Effect.READ)
def get(id: str = Field(description="Book id from books.search, for example b-007")) -> BookDetail:
    """Get one book: title, author, subject, year and branch. Use books.search to find ids."""
    book = CATALOG.get(id)
    if book is None:
        raise NotFound(f"No book with id '{id}'.", hint="Use books.search to find ids.")
    return BookDetail(
        id=book.id,
        title=book.title,
        author=book.author,
        subject=book.subject,
        year=book.year,
        branch=book.branch,
    )


if __name__ == "__main__":
    server.run()
