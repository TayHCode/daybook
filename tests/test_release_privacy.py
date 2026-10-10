# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Optional private deployment check using the independently frozen scanner."""
import importlib.util
import os
from pathlib import Path
import subprocess
import unittest


class ReleasePrivacyTests(unittest.TestCase):
    def test_core_has_no_private_terms_under_frozen_scanner(self):
        tool, denylist = os.environ.get('RELEASE_CHECK_TOOL'), os.environ.get('RELEASE_CHECK_DENYLIST')
        if not tool or not denylist:
            self.skipTest('Set external RELEASE_CHECK_TOOL and RELEASE_CHECK_DENYLIST for the private release test')
        spec = importlib.util.spec_from_file_location('frozen_release_check', tool)
        frozen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(frozen)
        scanner = frozen.Scanner(frozen.load_terms(Path(denylist)), [], [])
        root = Path(__file__).resolve().parents[1]
        names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
        for name in names:
            path = root / name
            if name and path.is_file():
                scanner.text(path.read_bytes(), 'tree', name)
        locations = [f"{row['path']}:{row['line']}" for row in scanner.findings if row['kind'] == 'denylist']
        self.assertEqual(len(locations), 0, f'Private-term count={len(locations)}; locations={locations}')
