# Durable model-call admission

Property: Every provider invocation consumes a unique durable attempt for its daily
slot: one initial attempt and at most one explicitly requested retry.

Harm: Losing an attempt record can duplicate a paid provider call after restart.

From the repository root, run:

```sh
bash formal/run.sh
```

No network, Python packages, real inputs, or provider credentials are needed.
Python 3.11+ suffices; the runner prefers `.venv/bin/python`. Set Java with:

```sh
export JAVA_HOME="<toolchain-directory>"
bash formal/run.sh
```

Java discovery uses explicit `FV_JAVA`, then `$JAVA_HOME/bin/java`, then `java`
on PATH, then an installed `temurin-*` directory under
`~/.local/share/fv-tools/`. The TLA+ artifact defaults to
`~/.local/share/fv-tools/tla2tools-v1.8.0.jar`; override with `FV_TLA_JAR`.
The jar must have SHA-256
`15d103b7e5617780bcc904c0af12f1ad242e2b345c0301136c5be4588f17edac`.
Missing tools or mismatched checksums fail closed; the runner never downloads.
Never put jars or JDKs in Git. Overrides change paths, not the jar pin.

The checked shared artifact identifies itself as **TLC2 Version
2026.10.15.145915 (rev: 932d35e)**; that banner is recorded rather than
assuming a version from the filename. Runs here used Temurin **21.0.12 (patch 1, build 1)** and
Python **3.14.4**. Java uses `-Xmx1g -XX:ActiveProcessorCount=1`; TLC uses
`-workers 1 -fp 0 -seed 1`. Each run has a 120-second timeout and a temporary
working directory that is removed afterward. Full commands appear in the logs.

Exit 0 means all current checks hold; exit 1 means an unexpected violation,
source drift, or error. Exit 2 is reserved for specifically recorded baseline
findings. There are no recorded baseline findings or accepted violations, so this
version never returns 2. A newly discovered violation fails with exit 1 until
reviewed; do not add a blanket expected-failure rule.

## Why this target and tool

This is a local evidence/brief application, with substantial parsers, native-ID
reconciliation, catch-up state, plugin consent checks, and optional paid provider
calls. Those areas were inspected alongside their tests. The narrow durable slot
contract is valuable because the user may retry after interruption without
knowing whether a provider was charged. Existing tests cover failed retries,
locking, and one KeyboardInterrupt; abrupt termination bypasses finally and has
more interruption points. A bounded TLA+ design model plus TLC fits ordering and
restart questions; concrete Python probes tie its critical boundaries to the code.
This is not a formal proof of the Python implementation.

## Scope and correspondence

`Admission.cfg` fixes two runners and two symbolic slots (`project`, `overview`),
both in one runtime directory on one fixed daily key namespace. Each runner can
restart arbitrarily many times, select either slot and retry flag on each run,
crash between any modeled steps, and see provider failure or completion. The
retry gate limits the reachable durable counter to two. No depth/state constraint
cuts off exploration. It is a safety check with no fairness/liveness claim.
The single TLC worker explores two modeled runners.

All actions and state in `Admission.tla` cite their source functions:

- `owner`, `pc`, `Acquire`, `Release`, `Crash`: `second_brain/daybook.py:run`.
  Nonblocking flock excludes competing holders; close/process exit releases it.
  Failed acquisition stutters because it cannot affect provider admission.
- `disk`, `local`, `Read`: `second_brain/calls.py:Ledger.__init__` and
  `second_brain/storage.py:read`. Whole-ledger snapshots matter: writes replace
  the entire calls.json, not just one row.
- `chosen`, `retry`, `Eligible`, `Admit`: `Ledger.slot` and `Ledger.call` in
  `second_brain/calls.py`. Empty slots admit once; an existing attempted/failed
  slot admits only with retry enabled and attempts < 2. Completed slots suppress
  invocation even with the flag. Prompt/cache contents do not create an invocation.
- `Persist`, `Finish`: `second_brain/calls.py:Ledger.call`, `Ledger.fail` and
  `second_brain/storage.py:write`. An in-memory update is separate from file
  persistence; the one atomic step is the completed `os.replace`. Crashes during
  temporary-file creation/write/flush/fsync occur before that step. A write error
  before admission aborts without invoking the provider.
- `calls`, `Invoke`: ghost counters of entry to `second_brain/model.py:CLIModel.summarize`
  via `second_brain/calls.py:Ledger.call`. Incrementing the ghost counter does not
  claim the provider succeeded, acknowledged, or billed exactly once internally.
- `Outcome`: `Ledger.call`/`Ledger.fail` and
  `second_brain/daybook.py:_model_states` validation failures. Saving a result
  before saving completion can leave an attempted slot; completed-to-failed
  validation can also permit the one explicit retry. Neither resets the counter.
- `AuthorizedAttempts`: the documented durable slot contract, implemented by
  `Ledger.call`; `calls[slot] <= disk[slot].attempts <= 2` at every reachable state.
  A sticky ghost `unauthorized` also rejects any invocation with attempt > 1
  and this runner's retry flag false; it strengthens the explicit-consent check.
  Retry eligibility follows the actual flag/status/count guard above.

The model splits operations at externally meaningful persistence/effect boundaries;
it does not call a whole Python function atomic. Preflight/deadline/no-call paths
can exit from `check`. Releasing/reacquiring between calls overapproximates
interference compared with a run holding the lock over several ledger calls;
the completed disk/local snapshot is identical before the next call.

Assumptions: cooperative callers enter through `daybook.run`, one stable lock inode
and runtime path, ordinary POSIX flock/atomic replace semantics on one local
filesystem, well-formed state initially empty, no external edits/deletions, trusted
boundary/plugins, fixed slot identifiers and date. A completed replace survives
**process** death. Power loss/reboot durability is not assumed: storage does not
fsync the parent directory. No nested/reentrant calls are modeled.

Not covered: other properties of parsers or catch-up reminders, changing clock/date
or project/overview keys, the aggregate 12-project daily cap, multi-host/network
filesystems, hostile actors/plugins, privacy policy, provider CLI flags, arbitrary
filesystem corruption, provider-side duplicate execution, or unlimited slots/runners.
The suite checks the stated finite design plus five concrete source scenarios,
not all Python inputs, every machine crash, or the entire application.

## Results and ability to fail

The clean baseline explored **7,249 generated / 2,976 distinct states**, queue 0,
search depth 31, with no invariant violation. Current single-worker combined model/probe runtimes and TLC timings are recorded in
`evidence/baseline.txt` and `evidence/restored-pass.txt`. Original pre-export evidence
may reflect two TLC workers; the publication runner uses one worker. No current-code finding is recorded.

`probe.py` uses the actual `daybook.run` lock, `Ledger.call`, and storage functions,
substituting `_run` only to avoid any collection and to select one synthetic slot.
Five cases cover abrupt `os._exit` at provider entry followed by ordinary restart,
explicit retry with another crash and third retry rejection, completed-slot reuse,
preflight recovery, and contention against a held real flock. Providers merely
append a synthetic effect line inside a temporary directory. No provider CLI runs.
These are correspondence checks, not an exhaustive Python verifier.

The cold skill procedure is `python3 formal/seed.py`. It makes a scratch removal
of the **real** pre-provider ledger write, confirms the stale-source guard fails,
then explicitly mirrors it as `PersistBeforeCall = FALSE` and refreshes scratch
hashes. TLC must report `AuthorizedAttempts` violated. The concrete product probe
must also fail. It restores exact original product/config/manifest bytes in finally
and demands a passing baseline. The script does not stage or commit anything.

The seeded counterexample in the code's words:

1. `Ledger.call` builds an attempted row only in memory; the scratch bug omits
   `write(calls.json)` before entering `model.summarize`.
2. The provider is entered with one effect and zero durable attempts. TLC fails
   immediately at `Invoke` (see its complete trace).
3. The real probe's provider abruptly terminates the process; Python finally does
   not run, so `calls.json` still has no attempt.
4. A restart without `--retry-failed` treats the slot as new and calls the provider
   again. The successful second call's finally saves attempts=1 for two effects.
5. Restoring the pre-call write leaves attempts=1 after the crash; ordinary restart
   suppresses a second provider invocation. Baseline is restored.

This seeded bug is **not present in current code**. The model switch is solely a
scratch mutation mechanism; committed configuration must stay TRUE.

Evidence (synthetic inputs only; actual tool output, with machine paths and dotted version formatting sanitized):

- `evidence/baseline.txt`: first successful suite, versions, commands, counts/runtime.
- `evidence/seeded-guard.txt`: hash drift rejects the real product edit.
- `evidence/seeded-product.patch`: exact temporary product deletion, never applied
  in the final tree.
- `evidence/seeded-fail.txt` and `evidence/counterexample.txt`: failing TLC output/trace.
- `evidence/seeded-product-fail.txt`: the same actual product bug duplicates effects.
- `evidence/restored-pass.txt`: passing suite after exact restoration.
- `evidence/cold-procedure.txt`: execution of the generated skill's procedure.
- `evidence/gitleaks.txt`: redacted full-history and working-tree scan counts.
- `evidence/tests-before.txt` / `evidence/tests-after.txt`: existing unit suite;
  optional external private checker disabled, 143 tests / one skip both before
  and after (142 passed each time).

For future changes use [the project skill](../.claude/skills/verify-model-admission/SKILL.md).
The hash guard runs first on every suite invocation. Review source/model
correspondence before updating hashes; a mismatch alone is not proof of a bug.
Keep findings, traces, counts, bounds and this README synchronized with changes.
