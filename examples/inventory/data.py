"""Deterministic fake cloud inventory shared by the lean and plain example servers."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

REGIONS = ["ap-south-1", "us-east-1", "eu-west-1", "ap-southeast-1"]
KINDS = ["vm", "bucket", "database", "function", "load_balancer"]
STATUSES = ["running", "running", "running", "stopped", "degraded"]
TEAMS = ["payments", "search", "platform", "data", "identity"]


def _resource(i: int, rng: random.Random) -> dict:
    kind = KINDS[i % len(KINDS)]
    region = REGIONS[(i // len(KINDS)) % len(REGIONS)]
    team = rng.choice(TEAMS)
    created = datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=rng.randint(0, 15000))
    rid = f"{kind[:2]}-{i:05d}"
    return {
        "id": rid,
        "arn": f"arn:cloud:{kind}:{region}:123456789012:{kind}/{rid}",
        "kind": kind,
        "name": f"{team}-{kind.replace('_', '-')}-{i:03d}",
        "region": region,
        "status": rng.choice(STATUSES),
        "created_at": created.isoformat(),
        "owner": f"{team}-oncall@example.com",
        "cost_per_month": round(rng.uniform(3, 2400), 2),
        "tags": {
            "team": team,
            "env": rng.choice(["prod", "staging", "dev"]),
            "cost-center": f"cc-{rng.randint(100, 999)}",
            "managed-by": rng.choice(["terraform", "pulumi", "console"]),
            "service": f"{team}-svc-{rng.randint(1, 9)}",
        },
        "config": {
            "instance_type": rng.choice(["m7.large", "c7.xlarge", "r7.2xlarge", "t4.small"]),
            "vpc_id": f"vpc-{rng.randrange(16**8):08x}",
            "subnet_ids": [f"subnet-{rng.randrange(16**8):08x}" for _ in range(3)],
            "security_group_ids": [f"sg-{rng.randrange(16**8):08x}" for _ in range(2)],
            "encryption": {"enabled": True, "key_id": f"key-{rng.randrange(16**12):012x}"},
            "backup": {"enabled": rng.random() > 0.3, "retention_days": rng.choice([7, 14, 30])},
            "monitoring": {"detailed": rng.random() > 0.5, "interval_seconds": 60},
            "maintenance_window": "sun:03:00-sun:04:00",
        },
        "metrics": {
            "cpu_p95": round(rng.uniform(1, 98), 1),
            "memory_p95": round(rng.uniform(5, 95), 1),
            "requests_per_minute": rng.randint(0, 50000),
            "error_rate": round(rng.uniform(0, 0.05), 4),
        },
    }


def load_resources(count: int = 400, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    return [_resource(i, rng) for i in range(count)]


RESOURCES = load_resources()
BY_ID = {r["id"]: r for r in RESOURCES}

# Metrics come from a separate, slower monitoring API in a real inventory. The counter
# lets tests check that concise calls never fetch them (spec RES-11).
METRICS_LOOKUPS = 0


def metrics_for(resource_id: str) -> dict:
    global METRICS_LOOKUPS
    METRICS_LOOKUPS += 1
    return dict(BY_ID[resource_id]["metrics"])
