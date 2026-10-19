"""Concrete correspondence checks using real flock/Ledger/storage and fake providers.

Not exhaustive verification of Python: TLC explores abstract orderings; these
checks exercise the source correspondence at the boundary most likely to drift.
All artifacts are synthetic, in TemporaryDirectory. No CLIModel, inputs or network.
"""
from datetime import datetime, timezone
import fcntl
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from second_brain import daybook
from second_brain.calls import Ledger
from second_brain.privacy import Boundary
from second_brain.stages import Stages
from second_brain.storage import read

SLOT = '2026-10-18:daybook:2026-10-17:synthetic'


def child(root, retry, mode):
    root = Path(root)

    class FakeProvider:
        def preflight(self):
            if mode == 'preflight-fails':
                raise FileNotFoundError('synthetic provider unavailable')

        def summarize(self, prompt):
            # A synthetic external effect survives abrupt process death.
            with (root / 'effects.txt').open('a') as handle:
                handle.write('synthetic provider entered\n')
                handle.flush()
                os.fsync(handle.fileno())
            if mode == 'crash':
                os._exit(73)  # no finally, unlike KeyboardInterrupt in repo tests
            return {'synthetic': True}, {}

    def just_ledger(config, boundary, model, date, now, path, stages,
                    retry_failed, no_model, refresh_sources, refresh_local):
        ledger = Ledger(path, datetime(2026, 10, 18, tzinfo=timezone.utc),
                        model, boundary, Stages(), config, time.monotonic(), retry_failed)
        ledger.call('daybook:2026-10-17:synthetic', 'synthetic prompt')
        return {'status': 'probe'}

    with patch.object(daybook, '_run', just_ledger):
        result = daybook.run({'runtime_dir': str(root), '_allow_network': True},
                             Boundary({}), FakeProvider(), retry_failed=retry)
    (root / 'last-status.txt').write_text(result['status'])


def execute(root, retry=False, mode='success'):
    process = multiprocessing.get_context('fork').Process(target=child, args=(str(root), retry, mode))
    process.start()
    process.join(10)
    if process.is_alive():
        process.kill()
        process.join()
        raise RuntimeError('Synthetic child timed out')
    expected = 73 if mode == 'crash' else 0
    if process.exitcode != expected:
        raise RuntimeError(f'Unexpected synthetic child exit {process.exitcode}, expected {expected}')


def counts(root):
    effects = root / 'effects.txt'
    n = len(effects.read_text().splitlines()) if effects.exists() else 0
    saved = read(root / 'calls.json', {}).get(SLOT, {}).get('attempts', 0)
    return n, saved


def verify(root, maximum, story):
    n, saved = counts(root)
    print(f'{story}: provider_entries={n}, durable_attempts={saved}, authorized_limit={maximum}', flush=True)
    if not (n <= saved <= maximum):
        raise RuntimeError('COUNTEREXAMPLE: provider entry lacks a unique durable authorized attempt; ' + story)


def run():
    with tempfile.TemporaryDirectory(prefix='daybook-formal-probe-') as temporary:
        base = Path(temporary)
        root = base / 'ordinary'
        execute(root, mode='crash')
        # Even the very first invocation must have a durable entry before effect.
        try:
            verify(root, 1, '1. Enter provider, then terminate process without finally')
        except RuntimeError as exc:
            # Continue once to show the user-visible duplicate on restart.
            print(str(exc), flush=True)
        execute(root)
        verify(root, 1, '2. Restart without --retry-failed')
        if counts(root) != (1, 1):
            raise RuntimeError('Crash/restart probe did not exercise exactly one call')

        root = base / 'retry'
        execute(root, mode='crash')
        execute(root, retry=True, mode='crash')
        execute(root, retry=True)
        verify(root, 2, 'Explicit retry after crash, crash again, third retry suppressed')
        if counts(root) != (2, 2):
            raise RuntimeError('Explicit retry probe did not exercise two calls')

        root = base / 'completed'
        execute(root)
        execute(root, retry=True)
        verify(root, 1, 'Completed slot never repeats, including --retry-failed')
        if counts(root) != (1, 1):
            raise RuntimeError('Completed-slot probe did not exercise cache reuse')

        root = base / 'preflight'
        execute(root, mode='preflight-fails')
        if counts(root) != (0, 0):
            raise RuntimeError('Preflight failure consumed a slot')
        execute(root)
        verify(root, 1, 'Preflight failure consumes nothing; later recovery gets first attempt')

        root = base / 'locked'
        root.mkdir()
        with (root / 'daybook.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            execute(root)
            if counts(root) != (0, 0) or (root / 'last-status.txt').read_text() != 'locked':
                raise RuntimeError('Locked runner entered ledger')
        execute(root)
        verify(root, 1, 'Contending runner returns locked; later runner calls once')
    print('Concrete correspondence: 5 scenarios passed', flush=True)
