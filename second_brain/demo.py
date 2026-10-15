# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Deterministic invented repos and transcripts; never scans user home data."""
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import subprocess
from . import daybook
from .privacy import Boundary
from .errors import UserError
from .storage import write

DEMO_NOW = datetime(2026, 10, 12, 9, tzinfo=timezone.utc)


def generate(destination):
    destination = Path(destination).resolve()
    # Reuse is restricted to a folder we created; never overwrite unfamiliar work.
    if destination.exists():
        if not (destination / '.synthetic-daybook').is_file():
            raise UserError('Demo destination is not a marked synthetic folder; choose a new folder.')
        return json.loads((destination / 'config.json').read_text())
    destination.mkdir(parents=True, mode=0o700)
    write(destination / '.synthetic-daybook', 'synthetic data only\n')
    repo = destination / 'repos' / 'sample-app'
    repo.mkdir(parents=True)
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'Demo User', 'GIT_AUTHOR_EMAIL': 'demo-user',
           'GIT_COMMITTER_NAME': 'Demo User', 'GIT_COMMITTER_EMAIL': 'demo-user',
           'GIT_AUTHOR_DATE': '2026-10-09T14:00:00Z', 'GIT_COMMITTER_DATE': '2026-10-09T14:00:00Z',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
    def git(*args):
        subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-C', str(repo), *args], check=True,
                       env=env, capture_output=True)
    git('init', '-q', '-b', 'demo-feature')
    write(repo / 'notes.md', 'Synthetic parser implementation\n')
    git('add', '--', 'notes.md')
    git('commit', '-q', '-m', 'Add Synthetic parser')
    write(repo / 'next.md', 'Synthetic pending documentation\n')
    transcripts = destination / 'transcripts'
    transcripts.mkdir()
    rows = [
        {'type': 'user', 'uuid': 'synthetic-owner-turn', 'timestamp': '2026-10-09T15:00:00Z', 'cwd': str(repo),
         'author': 'human', 'message': {'content': 'Implement the synthetic parser and check its tests.'}},
        {'type': 'assistant', 'timestamp': '2026-10-09T16:00:00Z', 'cwd': str(repo),
         'message': {'content': 'Synthetic parser is reported complete. Next: document error handling.'}},
        {'type': 'user', 'uuid': 'synthetic-unknown-turn', 'timestamp': '2026-10-09T17:00:00Z', 'cwd': str(repo),
         'message': {'content': 'This unmarked turn is deliberately unattributed.'}},
        {'type': 'user', 'uuid': 'synthetic-saturday', 'timestamp': '2026-10-10T12:00:00Z', 'cwd': str(repo),
         'author': 'human', 'message': {'content': 'Done: Run the synthetic parser regression.'}},
        {'type': 'user', 'uuid': 'synthetic-sunday', 'timestamp': '2026-10-11T12:00:00Z', 'cwd': str(repo),
         'author': 'human', 'message': {'content': 'Which synthetic error cases still need documentation?'}},
    ]
    write(transcripts / 'claude.jsonl', ''.join(json.dumps(r) + '\n' for r in rows))
    codex_rows = [
        {'type': 'session_meta', 'payload': {'id': 'synthetic-codex-session', 'cwd': str(repo)}},
        {'type': 'response_item', 'timestamp': '2026-10-09T18:00:00Z', 'payload': {
            'type': 'message', 'role': 'user', 'author': 'human', 'id': 'synthetic-codex-turn',
            'content': [{'type': 'input_text', 'text': 'Review synthetic error cases.'}]}},
    ]
    write(transcripts / 'codex.jsonl', ''.join(json.dumps(r) + '\n' for r in codex_rows))
    config = {'runtime_dir': str(destination / 'state'), 'timezone': 'UTC', 'model': 'none',
              'machines': [{'name': 'local', 'claude': [str(transcripts)], 'codex': [str(transcripts)]}],
              'local': {'machine': 'local', 'repo_roots': [str(repo.parent)]}}

    write(destination / 'state/briefs/2026-10-06.json', {
        'date': '2026-10-06', 'evidence_date': '2026-10-06', 'partial': False,
        'states': {'sample-app': {'evidence_date': '2026-10-06', 'state': {
            'open': ['Decide whether to extend synthetic parser examples.'], 'deferred': [],
            'start_here': {'step': '', 'where': {}, 'sources': []}}}}})

    write(destination / 'state/briefs/2026-10-09.json', {
        'date': '2026-10-09', 'evidence_date': '2026-10-09', 'partial': False,
        'states': {'sample-app': {'evidence_date': '2026-10-09', 'state': {
            'open': ['Document synthetic parser errors.'], 'deferred': ['Review optional synthetic telemetry.'],
            'start_here': {'step': 'Run the synthetic parser regression.', 'where': {}, 'sources': []}}}}})
    write(destination / 'config.json', config)
    return config


def run(destination):
    config = generate(destination)
    return daybook.run(config, Boundary({}), None, now=DEMO_NOW, no_model=True)
