# 0004: Pluggable tokenizer

Status: implemented for M1 (BUD-6, interface, tiktoken encodings and custom callables). Provider token-counting APIs are P1 and not part of this change. See [spec section 8.1.2](../spec.md).

## Problem

Token counts were tied to one tokenizer. `TokenCounter` could only wrap a tiktoken encoding, `OunceServer(tokenizer=...)` took only an encoding name, and `ouncemcp check` and `context` ignored the server's tokenizer and always counted with `o200k_base`. The numbers are a consistent estimate and not a host's bill, since model families tokenize differently (M0 report, "Known limitations"). A budget set against one tokenizer can be too loose or too tight for another, and a report that does not say which tokenizer produced a number cannot be compared with another report.

## Proposal

### Choosing a tokenizer

```python
from ouncemcp import OunceServer, get_counter

OunceServer("inventory")                                        # o200k_base, as before
OunceServer("inventory", tokenizer="cl100k_base")               # any tiktoken encoding
OunceServer("inventory", tokenizer=my_count, tokenizer_name="acme-tokenizer-2.1")   # a function
OunceServer("inventory", tokenizer=MyCounter())                 # a TokenCounter subclass

counter = get_counter(my_count, name="acme-tokenizer-2.1")      # outside a server
```

- `get_counter(tokenizer, *, name=None)` accepts an encoding name, a `TokenCounter`, or a callable `str -> int`. Encoding counters are cached. `name` is required for a bare callable and an error for anything else, because every report states the tokenizer used and a function has no name worth reporting.
- `TokenCounter` is now an abstract base class with a `name` and a `count(text)` method. `count_json`, and a default `truncate_text` that searches by prefix length, are provided. A subclass only implements `count`.
- `TiktokenCounter(encoding)` is the previous behavior, with the same exact truncation at token boundaries. An unknown encoding raises `ValueError` that lists the valid names and points to the callable option.
- `CallableCounter(fn, *, name)` wraps a function. Its result must be a non-negative integer, or `count` raises `ValueError` naming the tokenizer. Integer-like values (`__index__`) are accepted, floats and `None` are not.
- `TokenCounter.encoding_name` stays as an alias of `name`.

### Contract for custom counters

Shaping and truncation rely on one property: counts grow with the text. A prefix of a text must not have noticeably more tokens than the whole text (a few tokens of slack are fine, as at a token boundary). Counting words, characters or bytes, a vendor tokenizer, and any BPE tokenizer all satisfy this. A counter that is not monotonic can make shaping stop early or keep too much. Results are always checked with the counter's own `count`, so a result never goes over budget as that counter measures it. `tests/test_bud6_tokenizer.py` has a Hypothesis test of this for counters of different sizes.

### Reports name the tokenizer

| Report | Where the name appears |
| --- | --- |
| `ouncemcp check` | `Tokenizer` line in text output, `tokenizer` in `--json` (unchanged key, now the counter's name) |
| `ouncemcp context` | the token summary line ends with `counted with <name>` |
| `ServerCost` (`cost_report`, `measure_tools`) | `tokenizer` field, `to_dict()` and `render_text()` |
| Tool results | `_meta.ouncemcp.tokenizer`, next to `result_tokens` |
| Too-large errors | `_meta.ouncemcp.tokenizer`, next to `min_tokens` and `budget` |
| Startup summary on stderr | already named it |

`check` and `context` now count with the server's own tokenizer, and `--tokenizer ENCODING` overrides it with a tiktoken encoding. For a plain FastMCP server the default is `o200k_base`. An unknown encoding exits with status 2 and the list of valid names. Counting with a custom function from the command line is not supported, because it would mean loading arbitrary code from a flag. Set it on the server.

## Alternatives considered

- **Accept a callable without a name and use its `__name__`.** Rejected. Lambdas and partials give `<lambda>`, and two different functions often share a name, so reports could not be compared. A required name costs one argument.
- **Keep `TokenCounter` concrete and add a `count=` option.** Rejected. It would make one class both a tiktoken wrapper and a function wrapper, and subclassing for a vendor tokenizer would still need an escape hatch. An abstract base with two small implementations is simpler.
- **Accept tiktoken model names such as `gpt-4o`.** Deferred. The report would then name an encoding the user did not type. Encoding names are the stable identifier. A model-to-encoding helper can be added without changing this API.
- **A `--tokenizer` option that takes a Python path.** Rejected for the reason above.
- **Provider token-counting APIs now.** The spec makes them P1. They need network access, credentials and recorded responses for tests. The callable interface lets a user wrap one today, and a built-in adapter can follow.

## Impact

- **Default behavior is unchanged.** The default is still `o200k_base` and the inventory example still measures 488 definition tokens and 1,512 tokens for the four-task session.
- The same server counts differently under each tokenizer. The inventory example's definitions are 488 tokens with `o200k_base`, 454 with `cl100k_base`, and 500 with `p50k_base` and `gpt2`. Budgets are set against one tokenizer, so changing it can move a tool across the warn or fail thresholds.
- No added work per call. `TiktokenCounter.count` is the previous `count`.

Tests (`tests/test_bud6_tokenizer.py`): encodings match tiktoken, unknown encodings and misuse of `name`, caching and pass-through, callable validation and error messages, subclassing, truncation by prefix search (Hypothesis, against a chars/4 counter), the same server reported under three tokenizers with per-tool breakdowns that sum to the total, names in `check`, `context`, results and too-large errors, budgets enforced in the chosen tokenizer (a character counter keeps fewer items than `o200k_base`), shaping within budget for counters of different sizes (Hypothesis), and the CLI flag and the server's own tokenizer.

## Migration

- Code that built `TokenCounter("enc")` directly should call `get_counter("enc")` or `TiktokenCounter("enc")`. `TokenCounter` can no longer be instantiated, since it is abstract. Nothing in the repository did this outside `get_counter`.
- `ouncemcp check` on a server created with `tokenizer=` now reports that tokenizer. Before, it reported `o200k_base` whatever the server used.
- Clients reading `_meta.ouncemcp` get one more key, `tokenizer`.
