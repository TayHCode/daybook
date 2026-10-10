# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Resolve worker tools independently of the interactive shell's PATH."""
import os
from pathlib import Path
import shutil


def search_path(config):
    paths = config.get('executable_path', [str(Path.home()/'.local/bin'), str(Path.home()/'bin'),
                                          '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin'])
    return os.pathsep.join(str(Path(p).expanduser()) for p in paths)


def resolve(name, config):
    explicit = config.get('executables', {}).get(name)
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise FileNotFoundError('Configured executable unavailable')
    result = shutil.which(name, path=search_path(config))
    if result is None:
        raise FileNotFoundError('Worker executable unavailable')
    return result
