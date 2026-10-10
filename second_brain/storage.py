# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Private atomic artifacts and content-addressed change marks."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def fingerprint(value):
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return '.'.join(digest[i:i+8] for i in range(0,64,8))


def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        os.chmod(handle.name, 0o600)
        handle.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(handle.name, path)
