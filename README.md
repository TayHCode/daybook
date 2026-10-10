# daybook

A daily brief from your local git repositories and Claude Code/Codex transcripts:
what changed yesterday, where you left off, and one next step per active project.
Terminal and Markdown output. Plain local mode is the default; no account or model
is required. Python 3.11+ and git; Linux and macOS. Windows is unsupported in this
release because locking uses `fcntl`. Package metadata lists Linux/macOS support;
installers may still install on Windows. CLI startup prints a plain unsupported
message and exits with code 2 before importing platform-specific locking code.

## Quickstart

These commands support Linux and macOS. Windows is unsupported because locking
uses `fcntl`; this quickstart does not apply to Windows.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then clone
[the daybook GitHub repository](https://github.com/TayHCode/daybook) into a new
folder and enter it (`git clone https://github.com/TayHCode/daybook.git`, then
`cd daybook`). A downloaded source archive works too. The distribution is named
`daybook-core`; install it from this repository with uv. The PyPI package named
`daybook` is an unrelated reading-log app; this project is not distributed there.
uv may download the requested Python version when it is absent (as well as build
dependencies). These four commands install locally and run entirely invented data;
they do not open your existing repos or sessions:

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python .
.venv/bin/daybook demo --out daybook-demo
.venv/bin/daybook doctor --config daybook-demo/config.json
```

After the repository is public, a one-line install/try path is:

```sh
uvx --from git+https://github.com/TayHCode/daybook daybook demo --out ./daybook-demo
```

This GitHub command needs the repository to be public. For an existing local source
checkout, use `uvx --from /path/to/daybook daybook demo --out ./daybook-demo`.
The source build may create `build/`; it and the quickstart `daybook-demo/` are
ignored in this checkout. Keep any personal inputs outside the checkout.

The brief is printed and saved under `daybook-demo/state/briefs/` as Markdown and
JSON. Demo time is fixed; rerunning it reuses only its marked synthetic folder.
Choose a new destination to generate a new demo. No web server or scheduler is
installed. Open the Markdown in your editor or terminal.

## Your own local inputs

Create a config **outside your source checkout**, explicitly selecting each input.
`--repos` selects a parent folder of repos, a repo itself, or a linked git worktree.
Repeat flags for additional directories. Nothing defaults to your home sessions.
The main checkout and its linked worktrees share one project, named for the main
checkout; each worktree keeps its branch label. Collection resolves symlink paths
to their real paths; doctor displays missing roots exactly as configured.

```sh
.venv/bin/daybook init --config /your/private/daybook.json --repos /your/repos --claude /your/transcripts/claude --codex /your/transcripts/codex --timezone UTC
.venv/bin/daybook doctor --config /your/private/daybook.json
.venv/bin/daybook brief --config /your/private/daybook.json
```

Sources are read-only. Files under the private config's `.daybook-state` sibling
store selected evidence, snapshots and output; directories are 0700 and files 0600.
The default boundary leaves your local data intact: it is **not a secret scrubber**.
Keep these artifacts private. Optional external privacy callables can scrub data
before storage, model prompts and output. No private terms or personal configs are
shipped. Keep configs, caches and generated briefs outside version control.

## What it reads and sends

It reads only explicitly selected git repos/worktrees and JSONL roots. Git reads
branch refs and HEADs of selected detached checkouts (including local branch
membership for each collected commit), status
paths, commit times/authors/subjects and optional remote identity;
it never fetches, pushes or modifies your selected repositories. Transcript collection
reads message text and session cwd/branch/timestamp/provenance fields; bounded caches
retain selected text for ten days. A same-day git snapshot is reused until
`brief --refresh-local`. Transcript payload hashes detect supported corrections.
Source stamps include size, nanosecond modification/change times, inode and a content
probe: every byte up to 1 MiB, or head/tail and three interior samples above that.
An edit outside those samples with every metadata field unchanged can be missed.
Doctor stats root directories and resolves executables, without opening transcripts,
importing plugins, checking account authentication, or calling a model.
It names missing repository roots using the configured spelling, including symlinks,
so you can correct that path. Git-only worktrees with collected commits appear in
the brief even without sessions; detached commits carry a `detached HEAD` label.

Plain local mode has no network client or network subprocess. Nothing is transmitted
by the core unless you explicitly select a model and pass `--allow-network` for that
invocation, or select a network plugin and pass that flag. Installing uv/Python/build
dependencies may access package registries. Their downloads are separate from evidence
processing. The core installs no services, scheduled tasks, browser integrations,
telemetry, accounts or automatic posting.

## Optional model summaries

Authenticate your own Claude or Codex CLI separately. Add `executables` (absolute
paths), `executable_path` (a search-path list) or optional `model_id` to your private
config if needed. CLI usage/billing and provider retention follow your own account.
The selected, bounded evidence and previous summaries are sent to that CLI/provider:

```sh
.venv/bin/daybook brief --config /your/private/daybook.json --model claude --allow-network
.venv/bin/daybook brief --config /your/private/daybook.json --model codex --allow-network
```

Each adapter runs in an empty task folder with persistence disabled. Claude is
invoked with no tools or MCP servers. Codex uses read-only sandboxing, disabled shell,
MCP, apps, web search and agent tools, and ignores user configuration/rules. These
flags require a recent CLI; unsupported versions fail with an actionable error
that withholds raw provider output, and the brief falls back to deterministic fields. Managed CLI policy can still constrain
or affect a run; this is not an OS isolation boundary. Live provider calls are not
part of the synthetic self-test. See the official [Codex non-interactive guide](https://learn.chatgpt.com/docs/non-interactive-mode)
and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

A missing CLI is detected before admission and consumes no slot; installing or
configuring it permits another attempt without `--retry-failed`. Calls admitted for
execution consume durable daily slots: at most 12 project attempts
plus one overview. Late evidence can refresh facts while saved model prose lags until
the next eligible daily slot. `--retry-failed` permits one retry, with possible cost.
`--model none` always returns to deterministic local output and does not reuse saved
model prose. Missing sources and failed models are disclosed as partial coverage.
`brief --json` includes a content-free `reason` code and structured `reasons` list;
missing model CLIs report `model_cli_unavailable`. Full details remain in the brief.

CLI exit codes apply equally to text and `--json` output:

| Code | Meaning |
|---|---|
| 0 | Completed command; brief status may be written, refreshed, unchanged, partial, or locked. Partial means usable output with coverage gaps; locked means another run holds the state lock and no output was written. |
| 1 | Configuration/setup/runtime error, or doctor found missing roots or git. |
| 2 | Unsupported platform or invalid command-line arguments. |

Check `status` and `partial` in the JSON result, then inspect the saved brief for
coverage details. A missing model CLI produces partial output, exits 0, and consumes
no model slot.

## Attribution and limitations

Ordinary native `user` roles cannot distinguish typed human requests from agent relays.
They are counted as **unattributed**, excluded from owner totals, activity and prompts.
Only explicit `author`/`authored_by` values `human` or `owner` are credited, subject to
structural exclusions. Native formats often lack these fields: transcript-only inputs
may show no active projects despite real work. This conservative limitation is deliberate;
independent git activity can still establish a project as active. No transport identity,
text similarity, machine name or LLM judgment proves authorship. Assistant handoffs
for active projects are shown as unverified reports. Recognized pytest summaries
from native tool-result rows are retained as reported test counts, without raw tool
output or human credit. A passing claim contradicted by the last test summary in
the same transcript is called out for every handoff in the collected window,
even when a newer handoff exists or optional model prose is shown. Completed-change
claims without a matching collected repository commit are flagged as lacking
corroboration; uncommitted work and bounded or omitted history can explain the gap.
The next step prioritizes the last explicit human request and reported failures;
a handoff alone never establishes that a request is resolved. This is a bounded
rule check, not general verification of arbitrary claims or test frameworks.

Native message IDs are reconciled before counting mirrors. Within one file, repeated
IDs are a stream: the last occurrence supersedes intermediate text and stale mirrors.
Observed edits also establish order. Disagreeing files without an ordering link remain
a conflict; user text is unattributed, and the latest assistant handoff stays visible
with a conflict label. File mtimes and sync arrival never establish revision order.
Distinct IDs survive; equivalent mirrors retain source credit and available handoffs.
Each message uses its current cwd, inherited from the latest cwd-bearing native row
when absent. Cross-checkout sessions are split by checkout for project routing and
session detail; a later cwd change affects subsequent requests, handoffs and test
results. Coverage counts each contributing physical transcript once.

Collection is bounded to 4000 transcript files and 2 MB per line; oversized, partial,
malformed and omitted input is reported. File mtime selects the lookback inventory, so
old unchanged files can be absent. Model prompts are capped at 60000 characters and
sample details while preserving aggregates. The rule-based next step is a suggestion,
never permission to execute it. An empty input window writes honest empty sections.
The program does not prove tests ran, a merge deployed, or all machines synchronized.

## Extensions and development

Install only trusted local plugins you intentionally select. The API exposes sources,
Markdown sections and post-processors; see [docs/plugins.md](docs/plugins.md). Plugins
are arbitrary Python code, not sandboxed. Their declared network capability is checked
before import, but that declaration is a trust contract, not a firewall. Private
integrations belong in separately installed packages outside the core export.

```sh
.venv/bin/python -m unittest discover -s tests -q
.venv/bin/python -m compileall -q second_brain tests
```

The private release test activates only when `RELEASE_CHECK_TOOL` and
`RELEASE_CHECK_DENYLIST` point to an external frozen checker and private term list.
It reports counts and file:line locations only; portable tests skip that external
check when the inputs are unavailable. No denylist is included in this tree.

## Built on engineering-notebook by Prime Radiant (Apache-2.0)

Built on [engineering-notebook by Prime Radiant](https://github.com/prime-radiant-inc/engineering-notebook)
at commit `b0a34caa4b59e466652572310fd043a9548027c2`. The upstream Apache-2.0
[LICENSE](LICENSE) is retained verbatim; [NOTICE](NOTICE) and
[UPSTREAM.md](UPSTREAM.md) retain attribution. No upstream endorsement is implied.

Changes for daybook: explicit local input configuration, an offline daily-brief
CLI, conservative message attribution and native-ID reconciliation, synthetic
demo and metadata-only doctor, explicit network consent for optional model
summaries, and trusted plugin hooks. The original Bun notebook and web UI are
omitted. Modified files carry notices; [CHANGES.md](CHANGES.md) describes the
changes under Apache-2.0 section 4(b). Personal integrations live separately.

For a release checker that already creates an isolated Python environment, the
installation and synthetic checks are:

```sh
python -m pip install .
daybook demo --out daybook-demo
daybook doctor --config daybook-demo/config.json
python -m unittest discover -s tests -q
python -m compileall -q second_brain tests
```

The four-command uv quickstart above is the recommended end-user path on Linux/macOS.
