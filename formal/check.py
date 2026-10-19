"""Offline, bounded model check. Exit 2 is reserved for recorded findings (none)."""
import hashlib
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from guard import ROOT, check

JAR_SHA = '15d103b7e5617780bcc904c0af12f1ad242e2b345c0301136c5be4588f17edac'


def sanitize(output):
    output = re.sub(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3})\.(\d{1,3})\b',
                    r'\1 (patch \2)', output)
    output = re.sub(r'/tmp/tlc-[^/\s]+/', '<tlc-modules>/', output)
    return output.replace(str(Path.home()), '<home>')


def main():
    start = time.monotonic()
    check()
    tools = Path.home() / '.local/share/fv-tools'
    if os.environ.get('FV_JAVA'):
        java = Path(os.environ['FV_JAVA']).expanduser()
    elif os.environ.get('JAVA_HOME'):
        java = Path(os.environ['JAVA_HOME']).expanduser() / 'bin/java'
    elif shutil.which('java'):
        java = Path(shutil.which('java'))
    else:
        candidates = sorted(tools.glob('temurin-*/bin/java'))
        java = next((p for p in candidates if os.access(p, os.X_OK)), None)
    jar = Path(os.environ.get('FV_TLA_JAR', str(tools / 'tla2tools-v1.8.0.jar'))).expanduser()
    if java is None or not os.access(java, os.X_OK) or not jar.is_file():
        raise RuntimeError('Java/jar missing; set JAVA_HOME or FV_JAVA, and FV_TLA_JAR. Runner never downloads tools.')
    if hashlib.sha256(jar.read_bytes()).hexdigest() != JAR_SHA:
        raise RuntimeError('TLA+ jar SHA-256 mismatch')
    version = subprocess.run([str(java), '-version'], capture_output=True, text=True, check=True)
    print('Command: bash formal/run.sh', flush=True)
    print('Python: ' + sys.version.split()[0], flush=True)
    print(sanitize(version.stderr.strip()), flush=True)
    print('TLA+ v1.8.0 artifact SHA-256: ' + JAR_SHA, flush=True)
    print('Bounds: 2 runners, 2 fixed daily slots, initial + one explicit retry, process crashes/restarts; exhaustive safety, no liveness', flush=True)
    with tempfile.TemporaryDirectory(prefix='daybook-tlc-') as temporary:
        for name in ('Admission.tla', 'Admission.cfg'):
            shutil.copyfile(ROOT / 'formal' / name, Path(temporary) / name)
        args = [str(java), '-Xmx1g', '-XX:ActiveProcessorCount=1', '-cp', str(jar), 'tlc2.TLC',
                '-workers', '1', '-fp', '0', '-seed', '1', '-noGenerateSpecTE',
                '-config', 'Admission.cfg', 'Admission.tla']
        print('TLC command: "<resolved-java>" -Xmx1g -XX:ActiveProcessorCount=1 -cp "$FV_TLA_JAR" tlc2.TLC -workers 1 -fp 0 -seed 1 -noGenerateSpecTE -config Admission.cfg Admission.tla (temporary cwd)', flush=True)
        result = subprocess.run(args, cwd=temporary, capture_output=True, text=True, timeout=120)
        output = sanitize((result.stdout + result.stderr).replace(temporary, '<scratch>').replace(str(jar), '<tla-jar>'))
        print(output, end='', flush=True)
        if result.returncode != 0 or 'Model checking completed. No error has been found.' not in output:
            raise RuntimeError(f'TLC failed (exit {result.returncode}); no baseline findings are recorded')
    sys.path.insert(0, str(ROOT))
    from probe import run
    run()
    print(f'Result: PASS; property AuthorizedAttempts; runtime_seconds={time.monotonic()-start:.3f}', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = str(exc).replace(str(Path.home()), '~')
        print('FAIL: ' + message, file=sys.stderr, flush=True)
        raise SystemExit(1)
