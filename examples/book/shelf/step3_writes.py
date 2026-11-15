"""Shelf, step 3: borrowing and returning books safely (the book, chapter 6).

Adds write tools. Borrowing asks the person to confirm what exactly will happen,
and both writes are idempotent, so a retried call never borrows a book twice.
``build()`` makes a fresh server, which keeps tests independent.
"""

from __future__ import annotations

from typing import Annotated

from data import Library, Loan
from pydantic import BaseModel, Field

from ouncemcp import (
    Approver,
    Cursor,
    Detail,
    Effect,
    InvalidInput,
    NotFound,
    OunceServer,
    Paginated,
    PermissionDenied,
    ToolContext,
)

MEMBER = "demo-member"  # step 4 replaces this with the verified caller
BRANCH = "north"


class BookSummary(BaseModel):
    id: str
    title: str
    author: str
    year: Annotated[int | None, Detail] = None
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


class LoanOut(BaseModel):
    loan_id: str
    book_id: str
    title: str
    returned: bool


class Loans(BaseModel):
    loans: list[LoanOut] = Field(max_length=20)


def build(library: Library | None = None, *, approver: Approver | None = None) -> OunceServer:
    server = OunceServer(
        "shelf",
        instructions="A library. Search for books, borrow them and return them.",
        deps=library or Library(),
        approver=approver,
    )

    def loan_out(ctx: ToolContext, loan: Loan) -> LoanOut:
        book = ctx.deps.books[loan.book_id]
        return LoanOut(
            loan_id=loan.id, book_id=loan.book_id, title=book.title, returned=loan.returned
        )

    @server.tool(name="books.search", effect=Effect.READ)
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
            raise InvalidInput("The query is empty.", hint="Give at least one word.")
        found = [
            b
            for b in ctx.deps.books.values()
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

    @server.tool(name="books.get", effect=Effect.READ)
    def get(
        ctx: ToolContext,
        id: str = Field(description="Book id from books.search, for example b-007"),
    ) -> BookDetail:
        """Get one book: title, author, subject, year, branch and copies available.
        Use response_format=detailed for its summary."""
        book = ctx.deps.books.get(id)
        if book is None:
            raise NotFound(f"No book with id '{id}'.", hint="Use books.search to find ids.")
        return BookDetail(
            id=book.id,
            title=book.title,
            author=book.author,
            subject=book.subject,
            year=book.year,
            branch=book.branch,
            available=ctx.deps.available(book.id),
            summary=book.summary,
        )

    async def borrow_preview(book_id: str, ctx: ToolContext) -> str:
        book = ctx.deps.books.get(book_id)
        title = book.title if book else book_id
        return f"Borrow '{title}' ({book_id}) for 3 weeks? It will be due back on the 21st day."

    @server.tool(
        name="loans.borrow",
        effect=Effect.WRITE,
        approval="required",
        preview=borrow_preview,
        idempotency=True,
    )
    async def borrow(
        ctx: ToolContext,
        book_id: str = Field(description="Book id from books.search"),
    ) -> LoanOut:
        """Borrow a book for three weeks. The server asks the user to confirm, so call
        it directly. Side effect: creates a loan. Use loans.mine to see current loans."""
        book = ctx.deps.books.get(book_id)
        if book is None:
            raise NotFound(f"No book with id '{book_id}'.", hint="Use books.search to find ids.")
        if ctx.deps.available(book_id) < 1:
            raise PermissionDenied(
                f"All copies of '{book.title}' are on loan.",
                hint="Tell the user; suggest another book by the same author.",
            )
        return loan_out(ctx, await ctx.deps.borrow(book_id, MEMBER, BRANCH))

    @server.tool(name="loans.return", effect=Effect.WRITE, idempotency=True)
    async def give_back(
        ctx: ToolContext,
        loan_id: str = Field(description="Loan id from loans.mine, for example L-1"),
    ) -> LoanOut:
        """Return a borrowed book. Side effect: marks the loan returned and frees a copy.
        Use loans.mine to find loan ids."""
        loan = ctx.deps.loans.get(loan_id)
        if loan is None or loan.member != MEMBER:
            raise NotFound(
                f"No loan '{loan_id}' for you.", hint="Use loans.mine to see your loans."
            )
        return loan_out(ctx, loan if loan.returned else await ctx.deps.give_back(loan))

    @server.tool(name="loans.mine", effect=Effect.READ)
    def mine(ctx: ToolContext) -> Loans:
        """List your current loans, newest first. Use loans.return with a loan id."""
        loans = [l for l in ctx.deps.loans.values() if l.member == MEMBER and not l.returned]
        return Loans(loans=[loan_out(ctx, l) for l in reversed(loans)][:20])

    return server


server = build()

if __name__ == "__main__":
    server.run()
