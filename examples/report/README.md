# Report pilot

Long-running report (pilot 3 in spec section 15.1, milestone M2): background jobs, results by reference, ownership, expiry, and working across replicas and restarts.

```bash
ouncemcp check examples/report/server.py
python examples/report/server.py
```

Flow: `report.start` returns a job id at once; `jobs.get` returns `running`, then `done` with a short summary and a handle; `handles.read` pages through the 2,000 rows, staying inside the result budget.

| What the agent receives | Tokens (`o200k_base`) |
| --- | ---: |
| All 2,000 rows in one result (what a plain server would return) | 52,002 |
| `jobs.get` when done: summary and handle | 168 |
| One `handles.read` page of 50 rows | 1,351 |

| Guarantee | Test |
| --- | --- |
| Job ids and handles only work for the caller and tenant who started the report | `test_job_ids_and_handles_only_work_for_their_caller` |
| Two replicas and a restarted server sharing a store (and signing keys) serve the same job and rows | `test_two_replicas_and_a_restart_share_jobs_and_handles` |
| A job cut off by a restart reports `interrupted` instead of running forever | `test_a_job_lost_in_a_restart_reports_interrupted` |

All tests are in `tests/test_res5_handles_jobs.py`. In production, give every replica the same `store=RedisStore(...)` and `OUNCEMCP_CURSOR_KEYS`.

Not covered yet: resuming an interrupted job (it must be started again), and mapping jobs onto the MCP tasks extension.
