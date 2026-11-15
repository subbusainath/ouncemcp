"""The library behind Shelf, the project built in the OunceMCP book.

A small in-memory library: a catalog of books and a lending service with two
branches. Every write is logged in ``writes`` so tests can check what happened.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

BRANCHES = ("north", "south")
AUTHORS = [
    ("Ada Moreau", "Science"),
    ("Bo Lindqvist", "History"),
    ("Chen Wei", "Fiction"),
    ("Dara Okafor", "Science"),
    ("Elena Petrova", "Poetry"),
    ("Farid Haddad", "Fiction"),
    ("Grace Nakamura", "History"),
    ("Hugo Brandt", "Travel"),
    ("Ines Duarte", "Fiction"),
    ("Jonas Weber", "Science"),
]
WORDS = [
    "River",
    "Stone",
    "Light",
    "Garden",
    "Winter",
    "Harbor",
    "Silver",
    "Forest",
    "Signal",
    "Atlas",
    "Ember",
    "Tide",
]


@dataclass
class Book:
    id: str
    title: str
    author: str
    subject: str
    year: int
    branch: str
    copies: int
    summary: str


def make_catalog(count: int = 60, seed: int = 11) -> dict[str, Book]:
    rng = random.Random(seed)
    books = {}
    for i in range(1, count + 1):
        author, subject = AUTHORS[i % len(AUTHORS)]
        title = f"The {rng.choice(WORDS)} {rng.choice(WORDS)}"
        books[f"b-{i:03d}"] = Book(
            id=f"b-{i:03d}",
            title=title,
            author=author,
            subject=subject,
            year=1950 + (i * 7) % 75,
            branch=BRANCHES[i % 2],
            copies=1 + i % 3,
            summary=f"{title} is a {subject.lower()} book by {author}. " * 4,
        )
    return books


@dataclass
class Loan:
    id: str
    book_id: str
    member: str
    branch: str
    returned: bool = False


@dataclass
class Library:
    """The lending service. Writes are slow in real life; here they are logged."""

    books: dict[str, Book] = field(default_factory=make_catalog)
    loans: dict[str, Loan] = field(default_factory=dict)
    writes: list[str] = field(default_factory=list)
    review_lookups: int = 0

    def available(self, book_id: str) -> int:
        book = self.books[book_id]
        out = sum(1 for l in self.loans.values() if l.book_id == book_id and not l.returned)
        return book.copies - out

    async def borrow(self, book_id: str, member: str, branch: str) -> Loan:
        loan = Loan(id=f"L-{len(self.loans) + 1}", book_id=book_id, member=member, branch=branch)
        self.loans[loan.id] = loan
        self.writes.append(f"borrow {book_id} by {member}")
        return loan

    async def give_back(self, loan: Loan) -> Loan:
        loan.returned = True
        self.writes.append(f"return {loan.id}")
        return loan

    async def reviews(self, book_id: str) -> list[str]:
        """Reader reviews come from a separate, slow service in a real library."""
        self.review_lookups += 1
        return [f"Readers liked {self.books[book_id].title}.", "A good read for a rainy day."]
