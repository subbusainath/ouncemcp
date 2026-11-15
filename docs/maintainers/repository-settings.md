# Repository settings and going public

These steps are for the repository owner. They change GitHub settings, which no file in the repository can do. Do them in this order: protect first, then make the repository public.

## 1. Before switching to public: what was checked

The full git history (82 commits) was audited on 15 November 2026:

- **No secrets.** The only key-shaped strings are deliberate test fixtures in the redaction tests: AWS's documented `AKIAIOSFODNN7EXAMPLE` and a fake `ghp_...` token. No API keys were ever committed.
- **No personal data.** Every author and committer email is a GitHub or Anthropic `noreply` address, and no local file paths appear.
- **Nothing large.** The biggest files are `uv.lock` and the bundled report page (about 0.5 MB each).
- **Visible once public.** Commit messages contain `Claude-Session:` links. They lead to private sessions that only you can open.
- **Rotate pasted keys.** Two OpenRouter keys were pasted into a chat during development. They are not in the repository; revoke them at OpenRouter anyway.

## 2. Protect `main` (Settings → Rules → Rulesets)

Choose **New ruleset → Import a ruleset** and import `.github/rulesets/main.json`, then do the same with `.github/rulesets/release-tags.json`.

**`main.json` protects the default branch:**

- **No deletion and no force pushes.**
- **Every change through a pull request,** with all review threads resolved.
- **Required checks, run on the latest commit:**
    - `test (3.11)`, `test (3.12)` and `test (3.13)`: lint, format, types and tests;
    - `docs`: the strict docs build;
    - `server`: the MCP conformance suite.
- **Merge methods:** merge commits and squash.
- **No bypass,** not even for admins.

**`release-tags.json`** makes `v*` tags permanent: they cannot be moved or deleted.

**Reviews.** The ruleset requires 0 approvals, because GitHub does not let an author approve their own pull request, and today there is one maintainer. When a second maintainer joins, raise "Required approvals" to 1 and turn on "Require review from Code Owners" (`.github/CODEOWNERS`).

## 3. Other settings (before going public)

**Settings → General**

- **Pull requests:**
    - allow merge commits and squash merging;
    - turn off rebase merging;
    - turn on "Always suggest updating pull request branches";
    - turn on "Automatically delete head branches".
- **Features:** turn on Issues. Discussions is optional.

**Settings → Actions → General**

- **Actions permissions:** allow GitHub's own actions and selected ones; the workflows also use `astral-sh/setup-uv`, so allow it by name. "Require actions to be pinned to a full-length commit SHA" can be on: every `uses:` line is pinned to a commit, with the tag in a comment, and Dependabot updates the pins. A workflow that breaks one of these policies fails at startup with no jobs.
- **Workflow permissions:** "Read repository contents". The workflows also declare `contents: read`.
- **Fork pull request workflows:** "Require approval for first-time contributors" or stricter.
- **Pull requests from Actions:** don't allow GitHub Actions to create or approve them.

**Settings → Code security** (free for public repositories)

- Private vulnerability reporting: on. `SECURITY.md` points reporters there.
- Dependabot alerts and Dependabot security updates: on. `.github/dependabot.yml` sends weekly grouped updates.
- Secret scanning and push protection: on.
- CodeQL default setup for Python and JavaScript: on.

**Settings → Pages (optional)**

Publish the documentation site. The `docs` job already builds it in strict mode; a deploy workflow can be added once Pages is on.

## 4. Make the repository public

**Settings → General → Danger zone → Change visibility → Make public.**

GitHub asks you to confirm. Forks and stars from the private period do not exist, and the history audited above becomes visible.

## 5. After going public

- Check that the ruleset shows as **Active**: open a test pull request and confirm all five checks are required.
- Add a description, the website (the docs site) and topics (`mcp`, `model-context-protocol`, `python`, `fastmcp`, `llm-agents`) on the repository page.
- PyPI: when you are ready to release, add a trusted publisher for this repository in PyPI and a release workflow. No API token needs to be stored.
