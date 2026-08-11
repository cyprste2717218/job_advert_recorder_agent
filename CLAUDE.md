# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A [Google ADK 2.0](https://adk.dev/2.0/) `Workflow` (graph-based agent pipeline) that takes a job posting URL, extracts the job details via a headless Playwright/Chromium session, and appends them as a new row in the user's Excel workbook (via the [Composio MCP server](https://composio.dev/) using for the [Excel](https://docs.composio.dev/toolkits/excel)/[OneDrive](https://docs.composio.dev/toolkits/one_drive) integration).

Entry point: `agent.py` → `root_agent` (a `Workflow`). Run via `run_cli_select.py`, not `adk run` directly (see below).

## Commands

```powershell

# Activate the virtual env (needed to access uv tool to run the agent via the below command)
.venv\Scripts\Activate.ps1 

# Run the agent (interactive CLI with questionary select/multi-select UI)
uv run python run_cli_select.py .

# Install dev deps
uv sync --group dev

# Lint / format / type-check (also run automatically via pre-commit)
ruff check --fix .
ruff format .
pyright

# Install pre-commit hooks
pre-commit install
```

There is currently no test suite. Sanity-checking a module typically means `python -m py_compile <file>` or importing it directly, since ADK `Workflow`/`Agent` graphs fail at import/construction time if wired incorrectly.

`run_cli_select.py` monkeypatches `google.adk.cli.cli._prompt_for_function_call` so `RequestInput` events render as interactive `questionary` prompts (select/checkbox/text) instead of plain `input()`, then delegates straight into ADK's own `cli_run` — all of ADK's flags (`--replay`, `--resume`, `--jsonl`, `--save_session`, etc.) still work.

## Environment / secrets

Requires a `.env` (copy from `.env.example`) with `GOOGLE_API_KEY`, `COMPOSIO_API_KEY`, `COMPOSIO_USER_ID`. `agent.py` raises at import time if any are missing.

`config.json` (gitignored-adjacent, lives at repo root) caches the selected OneDrive drive/workbook/sheet/column-headers once the interactive setup flow has run, so subsequent runs skip straight to asking for a job URL. Delete it or hand-edit it to re-trigger setup or point at a different sheet.

## Architecture

The system is one big `Workflow` composed of nested `Workflow`s (sub-graphs), not a single flat graph. Each `Workflow` node can be a plain function, an async generator yielding `Event`s, or an ADK `Agent`. Routing between nodes is done via `Event(route=...)` returned from a "router" function, matched against a `{route_value: next_node}` dict in the parent `Workflow`'s edge list. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full node-by-node flowchart (nodes 0–15).

Composition, top to bottom:

- **`agent.py`** — top-level `root_agent`. Starts the browser, asks "add a job entry or halt", routes to either `response_job_agent` (job entry) or `response_end_node` (shutdown).
- **`sub_agents/handle_job_request_agent/agent.py`** — `response_job_agent`. Checks whether `config.json` already has drive/workbook/sheet/headers; if so loads them into `ctx.state` (`load_config_into_context`), otherwise runs the interactive setup flow (`handle_config_impl_agent.py`) to pick a OneDrive drive → workbook → sheet and cache the result to `config.json`. Either path converges on `response_job_url_fetch_node`.
- **`sub_agents/handle_job_request_agent/job_url_fetch_agent.py`** — `response_job_url_fetch_node`. Asks for the job URL, then runs an extract → verify loop: `extract_job_spec_details_agent` (Playwright tools: `navigate_page`/`read_page_text`/`click_page_element`) pulls a `{header: value}` record matching `{sheet_headers}`, then `verify_job_spec_details_agent` independently re-checks the source page and rejects fabricated/unsupported values, feeding issues back into the extractor's prompt via `{job_spec_verification_feedback?}` for a retry (capped at `MAX_EXTRACTION_ATTEMPTS = 3`). On success, hands off to `response_update_spreadsheet_node`.
- **`sub_agents/handle_job_request_agent/update_spreadsheet_node.py`** — `response_update_spreadsheet_node`. `write_job_record_agent` uses its own Composio `McpToolset` session (Excel toolkit: `EXCEL_GET_WORKSHEET_USED_RANGE`, `EXCEL_UPDATE_RANGE`) to append the record as a new row, retrying malformed structured output up to `MAX_WRITE_ATTEMPTS = 3`.
- **`sub_agents/end_system_node.py`** — `response_end_node`. Closes the Playwright context and halts.

Cross-cutting patterns worth knowing before touching any of the above:

- **Structured-output guarding.** Every LLM `Agent` with a Pydantic/dict `output_schema` gets an `after_model_callback=guard_structured_output(state_key)` that intercepts a non-JSON final response, stashes the raw text in `ctx.state[f"{state_key}_error"]`, and swaps in `"{}"` so schema validation doesn't crash with an opaque traceback. A paired check node (`raise_if_extraction_error` / `raise_if_write_error` / `checking_config_check_result`, depending on module) reads that stashed error immediately afterward and turns it into a bounded retry-then-raise. This exists in three near-identical copies (`handle_config_impl_agent.py`, `job_url_fetch_agent.py`, `update_spreadsheet_node.py`) — deliberately not shared, to avoid circular imports between those modules (each is documented inline with why).
- **Retry loops are all bounded** via a `ctx.state[attempts_key]` counter checked against a module-level `MAX_*_ATTEMPTS` constant, and reset to 0 on success. When adding a new retry loop, follow this pattern rather than looping unconditionally.
- **Two Composio `McpToolset` sessions exist independently** (one in `handle_config_impl_agent.py` for the setup/discovery flow, one in `update_spreadsheet_node.py` for the write). They are not shared, again to dodge a circular import (`response_job_agent` imports from `job_url_fetch_agent.py`, which imports from `update_spreadsheet_node.py`).
- **`browser_manager.py` is a module-level singleton**, not `ctx.state` — Playwright objects aren't JSON-serializable and `ctx.state` is persisted by the ADK session service. `launch()` is idempotent (Node 0a), `get_context()`/the page-level helpers (`navigate_page`, `read_page_text`, `click_page_element`) are used as `Agent` tools, and `close()` (Node 0d) is mirrored by an `atexit` hook so Ctrl+C doesn't orphan the headless Chromium subprocess.
- **`ctx.state` is the shared blackboard** across the whole graph — e.g. `job_url`, `sheet_headers`, `selected_workbook_id`, `selected_drive_id`, `selected_sheet_name`, `job_spec_details` are all written by one node and read by a later one via `{field_name}` template interpolation in an `Agent`'s `instruction` string (ADK's dynamic-state-placeholder syntax; `{field?}` for optional/possibly-empty state).
- Routing functions (`router_1`, `router_2`, etc.) are named identically across multiple modules — they're module-local and not shared, so don't assume a `router_1` in one file means anything in another.

## Git Conventions


### Branching

Each branch in this project should be focused on implementing changes that fall under a single specific change aim.

There are four types of aims, the aim of the branch is reflected in the prefix used in the branch name.
Every branch is linked to the github issue of the same name in the project board.

When naming a branch, ensure it matches the name of an existing github issue.
If you're making changes on an existing branch, make sure that the change is within the scope of the linked github issue.

The focus for a branch falls under one of the following:

- Feature:

For work that implements new functionality within the system. 
E.g. A new section of the CLI interface for re-configuring system settings

A feature branch should always follow the pattern `feat/[NAME]`, where `[NAME]` is a several word summary of the feature.

- **Chore:**

Improves system performance and/or implements dependency updates (i.e. breaking changes/security fixes) that don't impact testing suite deps.
E.g. Update to the next major version of `pydantic` and update all the sdk object imports and instances throughout the codebase.

A chore branch should always follow the pattern `chore/[NAME]`, where `[NAME]` is a several word summary of the chore.

- **Fix:**

Implementing a fix to an identified bug, e.g. fixing a google adk system runtime error that occurs on handling of unexpected user input.

A fix branch should always follow the pattern `fix/[NAME]`, where `[NAME]` is a several word summary of the fix.


- **Test:**

Adding and/or editing unit/integration/e2e tests, e.g. an eval suite of unit tests for an agent node which extracts and returns specific data from an info source.

A test branch should always follow the pattern `test/[NAME]`, where `[NAME]` is a several word summary of the feature.


- **Docs:**

Any changes or new additions to the project documentation. i.e. changes to `ARCHITECTURE.md`, `README.md` or `CLAUDE.md`
E.g. business logic change in source code for the google ADK system which need reflecting in the `mermaid.js` diagram in `ARCHITECTURE/md`.

A docs branch should always follow the pattern `docs/[NAME]`, where `[NAME]` is a several word summary of the docs changes.

### Commits

Before a git commit is created, staged and pushed to the remote branch, it is essential that the changeset meets the following requirements with occasional exceptions (detailed below):

#### - Keep the change small and focused:

The change should be small and focused, scoped to one specific type of change (refer to [Types of Git Commit](#types-of-git-commit) below to classify the change).
The commit message should clearly identify what the changes were in a good level of technical detail, covering what changed and where.

The commit message itself should generally be at most 50 characters in total, however if this length restricts a clear explanation of the changes these should be covered in the extended commit message. 

Commit messages should always start with one prefix from the [Types of Git Commit](#types-of-git-commit) section.
However, in the case the change doesn't neatly fall into any of these categories opt to classify it as a `chore` type.

##### - Types of Git Commit

The following types of git commits exist:

- `fix:`

For any changes which implement a bug fix, which could have been identified during implementation of a feature or pulled from a GitHub issue.

- `docs:`

For any project documentation changes, i.e. any `.md` files such as `CLAUDE.md` or `README.md`, contained within the source code of this project.

- `test:`

Any changes to or newly created test files or related config, i.e. relating to `vitest`, `playwright` or `supertest` testing frameworks


- `chore:`

For most changes which help implement code as part of an overarching feature which don't fall into any of the other pointed categories.


#### - Do not make too many changes

Does not modify more than 5 files and make more than 200 lines of code changes at once.
If the changeset exceeds this, separate out the changes into numerous commits to be sequentially pushed to the remote branch per the [guidance in the previous requirement](#--keep-the-change-small-and-focused)

#### - Pass the required git hooks

Ensures the `pre-commit.config.yaml` script is ran succesfully before a git commit is pushed.
However, on `feat/` branches in the case that the needed changes to make this hook scripts pass would exceed the change size requirement, add `WIP:` after the [git commit type prefix]().
e.g. `chore(WIP):`

This indicates that a developer should expect errors if they try to use the system at this commit hash.


## Current limitations (see README.md for detail)

- LinkedIn postings can't be scraped (anti-bot measures).
- Composio MCP invokes an LLM agent for what's otherwise a deterministic Excel/Graph API call, adding token and latency cost.
- No CLI-based way to change the tracked workbook/sheet/columns — requires manually editing or deleting `config.json`.
