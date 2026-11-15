"""Shelf, step 5: the complete library (the book, chapters 8 and 9).

Adds a circulation report that runs in the background and survives a restart, the
lending rules as a resource, each book as a resource, and a prompt for
recommendations. This is the file the later chapters test, measure and deploy.

From step 4: members, branches and librarians (chapter 7).

The caller now comes from the verified token: the member is the token's subject and
the branch is its tenant claim. Members see and change only their own loans and their
branch's books; librarians (scope ``librarian``) can list overdue loans. A local run
without a token (stdio) acts as a demo member of the north branch.
"""

from __future__ import annotations

from typing import Annotated

from data import BRANCHES, Library, Loan
from pydantic import BaseModel, Field

from ouncemcp import (
    Approver,
    Cursor,
    Detail,
    Effect,
    InvalidInput,
    JobResult,
    NotFound,
    OunceServer,
    Paginated,
    PermissionDenied,
    ToolContext,
)
from ouncemcp.security import require_scopes

LOCAL_MEMBER, LOCAL_BRANCH = "demo-member", "north"


def member(ctx: ToolContext) -> tuple[str, str]:
    """The verified member and branch. Without a token (a local stdio run) the caller
    is the demo member; a signed-in caller without a branch is refused."""
    identity = ctx.identity
    if not identity.authenticated:
        return LOCAL_MEMBER, LOCAL_BRANCH
    if identity.tenant not in BRANCHES:
        raise PermissionDenied("Your account has no library branch.", hint="Ask a librarian.")
    return identity.subject or identity.principal, identity.tenant


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


class ReportArgs(BaseModel):
    branch: str


class Circulation(BaseModel):
    branch: str
    books: int
    on_loan: int
    busiest: str | None


class ReportStarted(BaseModel):
    job: str
    message: str


LENDING_RULES = """Shelf lending rules

- Members borrow up to 5 books at a time, for 3 weeks.
- A book can be returned at either branch.
- Librarians can see every open loan at their branch.
"""


def build(library: Library | None = None, *, approver: Approver | None = None) -> OunceServer:
    server = OunceServer(
        "shelf",
        instructions="A library. Search for books, borrow them and return them.",
        deps=library or Library(),
        approver=approver,
        handles=True,  # large results by handle, and background jobs (chapter 8)
        # Policies also apply when the server runs over stdio (see chapter 7).
        stdio_policy="enforce",
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
        """Find books at your branch whose title or author contains every word of the
        query. Use response_format=detailed for year and subject, and books.get for one."""
        words = query.lower().split()
        if not words:
            raise InvalidInput("The query is empty.", hint="Give at least one word.")
        branch = member(ctx)[1]
        found = [
            b
            for b in ctx.deps.books.values()
            if b.branch == branch and all(w in f"{b.title} {b.author}".lower() for w in words)
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
        if book is None or book.branch != member(ctx)[1]:
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
        if book is None or book.branch != member(ctx)[1]:
            raise NotFound(f"No book with id '{book_id}'.", hint="Use books.search to find ids.")
        if ctx.deps.available(book_id) < 1:
            raise PermissionDenied(
                f"All copies of '{book.title}' are on loan.",
                hint="Tell the user; suggest another book by the same author.",
            )
        who, branch = member(ctx)
        if book.branch != branch:
            raise NotFound(f"No book with id '{book_id}' at your branch.", hint="Use books.search.")
        return loan_out(ctx, await ctx.deps.borrow(book_id, who, branch))

    @server.tool(name="loans.return", effect=Effect.WRITE, idempotency=True)
    async def give_back(
        ctx: ToolContext,
        loan_id: str = Field(description="Loan id from loans.mine, for example L-1"),
    ) -> LoanOut:
        """Return a borrowed book. Side effect: marks the loan returned and frees a copy.
        Use loans.mine to find loan ids."""
        loan = ctx.deps.loans.get(loan_id)
        if loan is None or loan.member != member(ctx)[0]:
            raise NotFound(
                f"No loan '{loan_id}' for you.", hint="Use loans.mine to see your loans."
            )
        return loan_out(ctx, loan if loan.returned else await ctx.deps.give_back(loan))

    @server.tool(name="loans.mine", effect=Effect.READ)
    def mine(ctx: ToolContext) -> Loans:
        """List your current loans, newest first. Use loans.return with a loan id."""
        who = member(ctx)[0]
        loans = [l for l in ctx.deps.loans.values() if l.member == who and not l.returned]
        return Loans(loans=[loan_out(ctx, l) for l in reversed(loans)][:20])

    @server.tool(name="loans.open", effect=Effect.READ, policy=require_scopes("librarian"))
    def open_loans(ctx: ToolContext) -> Loans:
        """List every open loan at your branch (librarians only). Use it to chase late books."""
        branch = member(ctx)[1]
        loans = [l for l in ctx.deps.loans.values() if l.branch == branch and not l.returned]
        return Loans(loans=[loan_out(ctx, l) for l in loans][:20])

    # Chapter 8: a background job. It is registered by name, so if the process running it
    # stops, the next jobs.get (on any replica sharing the store) starts it again.
    @server.job("reports.circulation", max_attempts=3)
    async def circulation(args: ReportArgs) -> JobResult:
        library: Library = server.deps
        books = [b for b in library.books.values() if b.branch == args.branch]
        rows = [
            {"book_id": b.id, "title": b.title, "on_loan": b.copies - library.available(b.id)}
            for b in books
        ]
        busiest = max(rows, key=lambda r: r["on_loan"], default=None)
        summary = Circulation(
            branch=args.branch,
            books=len(rows),
            on_loan=sum(r["on_loan"] for r in rows),
            busiest=busiest["book_id"] if busiest and busiest["on_loan"] else None,
        )
        return JobResult(summary=summary, items=rows)

    @server.tool(name="reports.start", effect=Effect.READ, policy=require_scopes("librarian"))
    async def start_report(ctx: ToolContext) -> ReportStarted:
        """Start the circulation report for your branch (librarians only), then poll
        jobs.get with the job id. Read the per-book rows with handles.read."""
        job = await ctx.start_job("reports.circulation", {"branch": member(ctx)[1]})
        return ReportStarted(job=job, message="Report started. Check it with jobs.get.")

    # Chapter 9: resources the host can show or attach, and a prompt.
    @server.resource("shelf://rules", mime_type="text/markdown")
    def rules() -> str:
        """The lending rules: how many books, for how long, and where to return them."""
        return LENDING_RULES

    @server.resource("shelf://books/{book_id}")
    def book_card(book_id: str, ctx: ToolContext) -> BookDetail:
        """One book from your branch's catalog, as JSON."""
        return get(ctx, id=book_id)

    @server.prompt()
    def recommend(subject: str) -> str:
        """Ask for three books on a subject, chosen from the catalog."""
        return (
            f"Recommend three books about {subject} from the Shelf catalog. Search with "
            "books.search, check details with books.get, and say which are available now."
        )

    return server


server = build()

if __name__ == "__main__":
    server.run()
