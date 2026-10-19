"""Run the documented cold mutation procedure, restoring only exact saved bytes."""
import json
from pathlib import Path
import subprocess
import sys

from guard import ROOT, check, hashes


def run(command, destination):
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=150)
    output = (result.stdout + result.stderr).replace(str(Path.home()), '~')
    (ROOT / 'formal/evidence' / destination).write_text(
        'Command: ' + ' '.join(command) + '\n' + output + f'\nExit: {result.returncode}\n')
    return result.returncode, output


def main():
    check()
    product = ROOT / 'second_brain/calls.py'
    manifest = ROOT / 'formal/source-hashes.json'
    config = ROOT / 'formal/Admission.cfg'
    original = {p: p.read_bytes() for p in (product, manifest, config)}
    anchor = b"        write(self.root / 'calls.json', self.budget)\n        self.attempts += 1"
    if original[product].count(anchor) != 1 or b'PersistBeforeCall = TRUE' not in original[config]:
        raise RuntimeError('Seed requires reviewed baseline source and TRUE configuration')
    evidence = ROOT / 'formal/evidence'
    evidence.mkdir(exist_ok=True)
    try:
        product.write_bytes(original[product].replace(anchor,
            b'        # SCRATCH BUG: pre-provider durable attempt write removed\n        self.attempts += 1'))
        diff = subprocess.run(['git', 'diff', '--', 'second_brain/calls.py'],
                              cwd=ROOT, capture_output=True, text=True, check=True)
        (evidence / 'seeded-product.patch').write_text(diff.stdout)
        code, output = run(['bash', 'formal/run.sh'], 'seeded-guard.txt')
        if code != 1 or 'Guarded source changed: second_brain/calls.py' not in output:
            raise RuntimeError('Source guard did not reject scratch mutation')
        config.write_bytes(original[config].replace(b'PersistBeforeCall = TRUE', b'PersistBeforeCall = FALSE'))
        manifest.write_text(json.dumps(hashes(), indent=2) + '\n')
        code, output = run(['bash', 'formal/run.sh'], 'seeded-fail.txt')
        if code != 1 or 'Invariant AuthorizedAttempts is violated.' not in output:
            raise RuntimeError('Mirrored product mutation did not fail the invariant')
        trace = output[output.index('Error: Invariant AuthorizedAttempts'):]
        (evidence / 'counterexample.txt').write_text(trace)
        code, output = run([sys.executable, '-c',
            "import sys; sys.path.insert(0, 'formal'); from probe import run; run()"], 'seeded-product-fail.txt')
        # Normalize command's interpreter to avoid committing a machine-local path.
        path = evidence / 'seeded-product-fail.txt'
        path.write_text(path.read_text().replace(sys.executable, 'python3').replace(str(Path.home()), '~'))
        if code != 1 or 'provider_entries=2, durable_attempts=1' not in output or 'COUNTEREXAMPLE' not in output:
            raise RuntimeError('Real product scratch mutation did not show duplicate provider effects')
        print('Scratch deletion caught by source guard, TLC invariant, and actual crash/restart probe.')
    finally:
        for path, data in original.items():
            path.write_bytes(data)
        print('Restored exact product, configuration, and manifest bytes.', flush=True)
    code, output = run(['bash', 'formal/run.sh'], 'restored-pass.txt')
    if code != 0:
        raise RuntimeError('Restored baseline did not pass')
    if any(path.read_bytes() != data for path, data in original.items()):
        raise RuntimeError('Restoration byte check failed')
    print('Cold procedure passed; restored baseline exit 0. Evidence: formal/evidence/')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('FAIL: ' + str(exc).replace(str(Path.home()), '~'), file=sys.stderr)
        raise SystemExit(1)
