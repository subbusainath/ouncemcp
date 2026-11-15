# Shelf: the book's example server

A lending library with two branches, built in five stages by [The OunceMCP Book](../../../docs/book/index.md). Every stage is a complete server that passes `ouncemcp check` with no lints, and `tests/test_book_shelf.py` tests what each chapter says it does.

| File | Chapter | Adds | Tools | Startup tokens |
| --- | --- | --- | --- | --- |
| `step1_catalog.py` | 2, 3 | Search and get, typed results, errors with hints | 2 | 297 |
| `step2_shaped.py` | 4, 5 | Paging with signed cursors, `Detail` fields, skipping work, deadlines | 2 | 420 |
| `step3_writes.py` | 6 | Borrow with approval and preview, idempotent writes, `deps` | 5 | 787 |
| `step4_secure.py` | 7 | Verified members, branch isolation, a librarian-only tool | 6 | 915 |
| `step5_complete.py` | 8, 9 | A resumable background report, resources, a prompt | 9 | 1,322 |
| `library.py` | | The library: 60 books, loans, a slow reviews service | | |
| `eval.yaml`, `eval_checks.py` | 11 | The task suite, with replay scripts | | |

```bash
ouncemcp dev examples/book/shelf/step1_catalog.py
ouncemcp check examples/book/shelf/step5_complete.py
ouncemcp eval examples/book/shelf/eval.yaml
```
