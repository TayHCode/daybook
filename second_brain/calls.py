# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Durable daily model admission: attempts consume slots before execution."""
import time
from pathlib import Path
from .errors import explanation
from .storage import fingerprint, read, write
try:
    from .privacy import allowed
except ImportError:
    def allowed(boundary, value, *, identity=''):
        return True


class Ledger:
    def __init__(self, root, now, model, boundary, stages, config, started, retry_failed):
        self.root, self.now, self.model = Path(root), now, model
        self.boundary, self.stages, self.config = boundary, stages, config
        self.started, self.retry_failed = started, retry_failed
        self.budget = read(self.root / 'calls.json', {})
        self.usages, self.failures = [], []
        self.attempts = 0
        self.preflight_checked = False
        self.preflight_failed = False

    def slot(self, key):
        return self.now.date().isoformat() + ':' + key

    def fail(self, key, stage, error_type):
        failure = {'stage': stage, 'error_type': error_type}
        self.failures.append(failure)
        if self.slot(key) in self.budget:
            self.budget[self.slot(key)].update(status='failed', **failure)
            write(self.root / 'calls.json', self.budget)

    def call(self, key, prompt):
        if self.preflight_failed:
            return None
        slot = self.slot(key)
        result_path = self.root / 'results' / (slot.replace(':', '.') + '.json')
        previous = self.budget.get(slot, {})
        attempts = previous.get('attempts', 0)
        retry = self.retry_failed and previous.get('status') in ('failed', 'attempted') and attempts < 2
        if previous and not retry:
            saved = read(result_path)
            if previous.get('status') == 'completed' and saved and saved['prompt_mark'] == fingerprint(prompt):
                return saved['value']
            if previous.get('status') in ('failed', 'attempted'):
                self.failures.append({'stage': previous.get('stage', 'model.project'), 'error_type': previous.get('error_type', 'Attempted')})
            return None
        remaining = self.config['worker_seconds'] - (time.monotonic() - self.started)
        if remaining < 30:
            self.failures.append({'stage': 'model.admission', 'error_type': 'Deadline'})
            return None
        try:
            if not self.preflight_checked and hasattr(self.model, 'preflight'):
                self.model.preflight()
            self.preflight_checked = True
        except Exception as exc:
            self.preflight_failed = True
            self.failures.append({'stage': 'model.preflight', 'error_type': type(exc).__name__,
                                  'message': explanation(exc)})
            if getattr(exc, 'code', None):
                self.failures[-1]['code'] = exc.code
            return None
        if hasattr(self.model, 'timeout'):
            self.model.timeout = min(self.config['model_timeout'], remaining - 15)
        self.budget[slot] = {'status': 'attempted', 'at': self.now.isoformat(), 'attempts': attempts + 1}
        write(self.root / 'calls.json', self.budget)
        self.attempts += 1
        stage = 'model.overview' if key.startswith('daybook-overview.') else 'model.project'
        try:
            with self.stages.at(stage):
                value, usage = self.model.summarize(self.boundary.clean(prompt))
                if not value:
                    raise ValueError('Empty output')
                if not allowed(self.boundary, value):
                    raise ValueError('Result rejected')
                value = self.boundary.tree(value)
            write(result_path, {'prompt_mark': fingerprint(prompt), 'value': value})
            self.usages.append({'key': key, **usage})
            self.budget[slot].update(status='completed', usage=usage)
            return value
        except Exception as exc:
            self.fail(key, getattr(exc, 'brief_stage', stage), type(exc).__name__)
            return None
        finally:
            write(self.root / 'calls.json', self.budget)
