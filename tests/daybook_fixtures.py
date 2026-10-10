# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Synthetic inputs and injectable adapters; never reads a real source."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo
from second_brain.dayconfig import configure, window


class Boundary:
    def clean(self, text):
        if 'SYNTHETIC_POISON' in text:
            raise ValueError('Synthetic rejection')
        return text.replace('SYNTHETIC_PRIVATE', 'generic')

    def tree(self, value):
        if isinstance(value, str):
            return self.clean(value)
        if isinstance(value, list):
            return [self.tree(v) for v in value]
        if isinstance(value, dict):
            return {self.clean(k): self.tree(v) for k, v in value.items()}
        return value


def claude(text='Build the synthetic feature', role='user', at='2026-10-07T13:00:00Z', **extra):
    # General fixtures represent independently typed owner requests. Attribution
    # probes explicitly pass author=None to model an ambiguous native envelope.
    return {'type': role, 'timestamp': at, 'cwd': '/synthetic/user/projects/demo',
            'gitBranch': 'feature', 'author': 'human' if role == 'user' else 'agent',
            'message': {'content': text}, **extra}


def codex(text='Build the synthetic feature', role='user', at='2026-10-07T13:00:00Z', **extra):
    return {'type': 'response_item', 'timestamp': at,
            'payload': {'type': 'message', 'role': role, 'author': 'human' if role == 'user' else 'agent',
                        'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': text}], **extra}}


def save(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


class FakeGH:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        value = self.responses.get(args[-1], [])
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))


class FakeGit:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        value = self.responses.get(tuple(args[4:]), '')
        return SimpleNamespace(returncode=0, stdout=value)


class Model:
    def __init__(self):
        self.calls = []

    def summarize(self, prompt):
        from second_brain.daybook import STATE_INSTRUCTION
        self.calls.append(prompt)
        if prompt.startswith(STATE_INSTRUCTION):
            data = json.loads(prompt[len(STATE_INSTRUCTION):])
            ids = [e['id'] for e in data['evidence']]
            value = {'yesterday': 'I worked on the synthetic feature.', 'stands': ['The change is reported, not verified.'],
                     'start_here': {'step': 'Review the synthetic feature.', 'why': 'The handoff needs verification.',
                                    'where': {}, 'sources': ids[:1]}, 'open': [], 'deferred': [], 'sources': ids}
        else:
            value = {'headline': 'I worked on the supplied projects.', 'order': [], 'x': None}
        return value, {'elapsed_seconds': 0, 'usage': {'input_tokens': 10, 'output_tokens': 5}}


class Case(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, HOME=str(self.root))
        env.start()
        self.addCleanup(env.stop)
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)
        self.boundary = Boundary()
        self.now = datetime(2026, 10, 8, 7, tzinfo=ZoneInfo('America/New_York'))
        self.config = configure({'runtime_dir': str(self.root / 'runtime'), 'privacy': {'callable': 'fake:clean'},
                                 '_allow_network': True, 'machines': [{'name': 'machine-a', 'claude': [str(self.root / 'claude')], 'codex': []}],
                                 'local': {'machine': 'machine-a'}, 'projects': [{'id': 'demo', 'aliases': ['demo']}],
                                 'executables': {'git': shutil.which('true')}})
        self.config['timezone'] = 'America/New_York'
        self.win = window(self.config, 'yesterday', self.now)
        self.registry = SimpleNamespace(resolve=lambda cwd, machine: 'demo' if '/demo' in cwd else None)
