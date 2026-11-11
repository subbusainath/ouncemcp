"""A fake ticketing API shared by the OunceMCP and plain pilot servers.

It behaves like a real one in the ways that matter for safe writes: every write takes
time, and ``latency_s`` can be raised to make a write finish after the caller has
given up, which is how duplicate writes happen in practice.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class Ticket:
    id: str
    tenant: str
    title: str
    status: str = "open"
    assignee: str | None = None
    body: str = ""


@dataclass
class TicketAPI:
    latency_s: float = 0.0
    tickets: dict[str, Ticket] = field(default_factory=dict)
    writes: list[str] = field(default_factory=list)

    def seed(self) -> TicketAPI:
        for i, (tenant, title) in enumerate(
            [
                ("acme", "Printer on floor 3 is jammed"),
                ("acme", "VPN drops every hour"),
                ("acme", "Laptop battery swelling"),
                ("globex", "Payroll export fails"),
            ],
            start=1,
        ):
            self.tickets[f"T-{i}"] = Ticket(id=f"T-{i}", tenant=tenant, title=title)
        return self

    async def search(self, tenant: str, status: str | None) -> list[Ticket]:
        return [
            t
            for t in self.tickets.values()
            if t.tenant == tenant and (status is None or t.status == status)
        ]

    async def get(self, tenant: str, ticket_id: str) -> Ticket | None:
        ticket = self.tickets.get(ticket_id)
        return ticket if ticket is not None and ticket.tenant == tenant else None

    async def create(self, tenant: str, title: str, body: str) -> Ticket:
        await asyncio.sleep(self.latency_s)
        ticket = Ticket(id=f"T-{len(self.tickets) + 1}", tenant=tenant, title=title, body=body)
        self.tickets[ticket.id] = ticket
        self.writes.append(f"create {ticket.id}")
        return ticket

    async def close(self, ticket: Ticket) -> Ticket:
        await asyncio.sleep(self.latency_s)
        ticket.status = "closed"
        self.writes.append(f"close {ticket.id}")
        return ticket

    async def delete(self, ticket: Ticket) -> None:
        await asyncio.sleep(self.latency_s)
        del self.tickets[ticket.id]
        self.writes.append(f"delete {ticket.id}")
