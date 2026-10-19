---
name: verify-model-admission
description: Verify daybook's durable model-call admission across process crashes and overlapping runs. Use when changing the runtime lock, calls.json writes, per-slot retries, or provider invocation order.
---

# Verify durable model-call admission

Property: Every provider invocation consumes a unique durable attempt for its daily
slot: one initial attempt and at most one explicitly requested retry.
Harm: Losing an attempt record can duplicate a paid provider call after restart.

## When this applies

Guarded files (entire file contents, SHA-256 in `formal/source-hashes.json`):
- `second_brain/daybook.py`: `run` locks before `_run` constructs `Ledger`;
  `_model_states` builds project/overview keys, calls and validation failures.
- `second_brain/calls.py`: `Ledger.__init__`, `slot`, `call`, `fail`; reads,
  retry gate, preflight, attempted write, provider invocation, final writes.
- `second_brain/storage.py`: `read` and `write`; flush/fsync data, then replace
  the whole JSON file atomically.
- `second_brain/model.py`: `CLIModel.summarize`; external provider boundary.

Start from the repository root. Read these functions and `formal/README.md`, then
`formal/Admission.tla`, `formal/Admission.cfg`, `formal/guard.py`,
`formal/check.py`, `formal/probe.py`, and `formal/seed.py`. The guarded hashes
are a review trigger, not proof of Python/model equivalence.

## Run and interpret

1. Run `bash formal/run.sh`. It uses `.venv/bin/python` when available, otherwise
   Python 3.11+; no Python dependencies or installation are required. It uses
   `FV_JAVA`, `JAVA_HOME`, or Java on PATH, with the shared-tool fallback and
   `FV_TLA_JAR` documented in the README,
   checks the jar checksum, caps TLC at one worker and 1 GiB, and never downloads.
2. Exit 0 means the bounded model and concrete correspondence scenarios passed.
   Exit 1 means source drift, unexpected violation or tooling failure; read the
   printed diagnostic. Exit 2 is reserved for recorded baseline findings; there
   are **no recorded findings**, so no violation is currently accepted as exit 2.
   A syntax error, timeout or checksum failure is never a finding or a pass.
3. On hash drift, compare changed code to each cited action. Check that the lock
   still covers ledger construction and provider execution, whole-file replacement
   remains atomic, attempts are persisted before invocation, and retries still
   require the flag, failed/attempted status and attempts < 2. Split an action if
   the code's atomic boundaries change. Never refresh hashes merely to get green.
4. If behavior intentionally changes, edit the model and concrete probe to match,
   preserving the property. Then refresh hashes with:
   ```sh
   python3 - <<'PY'
   import json, sys
   from pathlib import Path
   sys.path.insert(0, 'formal')
   from guard import hashes
   Path('formal/source-hashes.json').write_text(json.dumps(hashes(), indent=2) + '\n')
   PY
   ```
   A genuine violation stays a finding: write the real-code steps and bounds in
   `formal/README.md`, save its trace, and stop for review. Do not loosen the
   invariant, increase the attempt allowance, or fix product code without a request.
   If recording a baseline finding, implement exit 2 only for that exact reviewed
   counterexample signature; all new errors must still exit 1.

## Cold validation / ability to fail

Run `python3 formal/seed.py` once after building or changing this verification.
Use only a clean guarded-source baseline and the checked-in TRUE configuration.
The script refuses unexpected source hashes/configuration. It saves exact original
bytes, removes the pre-provider `write(calls.json)` from real `Ledger.call`, and:

1. Runs `bash formal/run.sh` without refreshing hashes: source guard must fail.
2. Mirrors the scratch deletion as `PersistBeforeCall = FALSE` and refreshes
   scratch hashes after that explicit review. Runs `bash formal/run.sh` again:
   TLC must fail `AuthorizedAttempts` with provider count 1, durable attempts 0.
3. Runs the concrete probe on that same changed product code: a child exits via
   `os._exit` inside the fake provider (no finally), and restart without
   `--retry-failed` repeats the call. Two effects share only one saved attempt.
4. Restores product, hashes and configuration in `finally`, then reruns
   `bash formal/run.sh`; baseline must pass. Temporary edits must never be staged.

Logs, scratch product diff and the extracted TLC trace are saved under
`formal/evidence/`. Read them; a hash mismatch alone is not the required failing
model check. If interrupted by uncatchable termination, use the printed patch and
`git diff` to identify only this exact scratch removal; preserve unrelated work.
Never reset or clean the checkout. Run the repo tests before and after:
`.venv/bin/python -m unittest discover -s tests -q` (or `python3` without a venv).
Use only synthetic data; do not invoke configured provider CLIs or private inputs.

## Limits to preserve in handoff

TLC explores all reachable states of two runners, two fixed daily slots, and
process crashes/restarts with stable atomic filesystem replacements. It checks
safety, not eventual progress. It does not prove the Python implementation,
provider exactly-once execution, machine/power-loss durability (no directory
fsync), changing dates/keys, multi-host filesystems, deleted/tampered state,
malicious plugins, daily aggregate project caps, or privacy/authorization of
provider CLI configuration. Read the README's assumptions before making claims.
