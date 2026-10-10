# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Content-free journal diagnostics, including handled partial failures."""
from contextlib import contextmanager
import json
import sys
import traceback
from .errors import explanation


class Stages:
    def __init__(self, debug=False):
        self.debug = debug

    @contextmanager
    def at(self, name):
        if self.debug:
            print(json.dumps({'stage': name, 'status': 'started'}), file=sys.stderr, flush=True)
        try:
            yield
        except Exception as exc:
            if not hasattr(exc, 'brief_stage'):
                exc.brief_stage = name
            record = {'stage': name, 'error_type': type(exc).__name__, 'message': explanation(exc)}
            if self.debug:
                # Function names/line numbers only: no paths, locals, source lines or messages.
                record['frames'] = [{'function': f.name, 'line': f.lineno}
                                    for f in traceback.extract_tb(exc.__traceback__)]
            print(json.dumps(record), file=sys.stderr, flush=True)
            raise
