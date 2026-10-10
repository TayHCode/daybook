# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Required application-supplied boundary, applied before storage/model/output."""
import importlib
import sys


class Boundary:
    def __init__(self, config=None):
        config = config or {}
        if not config:
            self.clean = lambda text: text
            return
        if config.get('code_path'):
            sys.path.insert(0, config['code_path'])
        module, function = config['callable'].split(':', 1)
        self.clean = getattr(importlib.import_module(module), function)
        if not callable(self.clean):
            raise ValueError('A privacy boundary is required')

    def tree(self, value):
        if isinstance(value, str):
            return self.clean(value)
        if isinstance(value, list):
            return [self.tree(item) for item in value]
        if isinstance(value, dict):
            return {self.clean(key): self.tree(item) for key, item in value.items()}
        return value
