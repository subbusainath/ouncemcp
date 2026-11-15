# Releasing

Releases are published to PyPI by `.github/workflows/release.yml` when a version tag is pushed. It uses [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/), so no PyPI token is stored anywhere: PyPI checks that the upload comes from this repository's release workflow.

## One-time setup (owner)

1. **PyPI project.** Sign in to pypi.org, open *Your account → Publishing → Add a new pending publisher*, and enter:

    | Field | Value |
    | --- | --- |
    | PyPI project name | `ouncemcp` |
    | Owner | `subbusainath` |
    | Repository name | `ouncemcp` |
    | Workflow name | `release.yml` |
    | Environment name | `pypi` |

    The first successful release creates the project. Optionally, do the same on test.pypi.org to rehearse.

2. **GitHub environment.** In *Settings → Environments*, create `pypi`. Add yourself as a **required reviewer**, so every upload waits for your approval, and under *Deployment branches and tags* allow only tags matching `v*`.

3. **Tag protection.** The `release-tags` ruleset (`.github/rulesets/release-tags.json`) stops version tags from being moved or deleted once pushed.

## Cutting a release

1. **Check the gates.** Every release gate in spec section 14.2 must pass:
    - CI is green on `main`.
    - Gate 6, the accuracy check: run `ouncemcp bench benchmarks/bench.yaml --compare benchmarks/results/m3-bench.json --fail-on-regression`. It costs about $2.20 in model calls.
    - Gate 7, the performance check: run `benchmarks/scripts/m4_release_perf.py`.
2. **Set the version** in both `pyproject.toml` (`version`) and `src/ouncemcp/__init__.py` (`__version__`). The workflow refuses a tag that does not match both.
3. **Update `CHANGELOG.md`.** Turn the "unreleased" heading into the version and date. For 1.0, also change the `Development Status` classifier in `pyproject.toml` to `5 - Production/Stable`.
4. **Merge** those changes to `main` through a pull request.
5. **Tag and push** from the merged `main`:

    ```bash
    git tag -a v1.0.0 -m "OunceMCP 1.0.0"
    git push origin v1.0.0
    ```

6. **Approve** the `pypi` deployment when GitHub asks. The workflow:
    - builds the wheel and the source package;
    - installs the wheel into a clean environment and checks the version;
    - uploads both files to PyPI.
7. **Create a GitHub release** from the tag, with the CHANGELOG entry as its notes.

## If something goes wrong

PyPI never lets a file be replaced. If an upload is wrong, fix it on `main` and release the next patch version. If the problem is serious, *yank* the bad version on PyPI. Never move or reuse a tag.
