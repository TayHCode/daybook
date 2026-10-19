"""Fail closed on source drift; refreshing hashes requires a model review."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARDED = ('second_brain/calls.py', 'second_brain/daybook.py',
           'second_brain/storage.py', 'second_brain/model.py')


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in GUARDED}


def check():
    saved = json.loads((ROOT / 'formal/source-hashes.json').read_text())
    if set(saved) != set(GUARDED):
        raise RuntimeError('Guard manifest must contain exactly the guarded files')
    changed = [name for name, digest in hashes().items() if saved[name] != digest]
    if changed:
        raise RuntimeError('Guarded source changed: ' + ', '.join(changed) +
                           '; follow .claude/skills/verify-model-admission/SKILL.md')
    print('Source guard: 4/4 SHA-256 matches')


if __name__ == '__main__':
    check()
