# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Consent and synthetic operation tests written before the new CLI."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from second_brain import cli, daybook, sources
from second_brain.dayconfig import configure
from second_brain.privacy import Boundary


class SafetyTests(unittest.TestCase):
    def test_network_plugin_rejected_before_import(self):
        config = configure({'runtime_dir': 'unused', 'plugins': [
            {'module': 'synthetic_plugin:Plugin', 'capabilities': ['network']}]})
        with patch('second_brain.sources.importlib.import_module', side_effect=AssertionError('No import')):
            with self.assertRaises(ValueError):
                sources.Reader(config, Boundary({}), Path('unused'))

    def test_model_rejected_before_collection_or_state_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = configure({'runtime_dir': str(Path(tmp) / 'state')})
            with patch.object(daybook.sources, 'repos', side_effect=AssertionError('No collection')):
                with self.assertRaises(ValueError):
                    daybook.run(config, Boundary({}), object(), no_model=False)
            self.assertFalse((Path(tmp) / 'state').exists())

    def test_demo_reads_no_home_transcripts_or_network_and_rerun_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = (Path(tmp) / 'demo').resolve()
            output = io.StringIO()
            with patch('second_brain.model.CLIModel.summarize', side_effect=AssertionError('No model')), redirect_stdout(output):
                self.assertEqual(cli.main(['demo', '--out', str(destination)]), 0)
            config = json.loads((destination / 'config.json').read_text())
            self.assertTrue(all(Path(p).is_relative_to(destination) for m in config['machines'] for t in ('claude', 'codex') for p in m.get(t, [])))
            self.assertTrue(all(Path(p).is_relative_to(destination) for p in config['local']['repo_roots']))
            self.assertIn('Synthetic', output.getvalue())
            from second_brain.demo import DEMO_NOW
            result = daybook.run(config, Boundary({}), None, now=DEMO_NOW, no_model=True)
            self.assertEqual((result['status'], result['calls']), ('unchanged', 0))

    def test_doctor_never_opens_transcript_payloads_or_imports_plugins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'transcripts').mkdir()
            (root / 'transcripts/a.jsonl').write_text('must never be read')
            config = configure({'runtime_dir': str(root / 'state'), 'machines': [{'name': 'local', 'claude': [str(root / 'transcripts')]}], 'plugins': [{'module': 'uninstalled:Plugin'}]})
            with patch.object(Path, 'read_text', side_effect=AssertionError('No content read')), patch.object(sources.importlib, 'import_module', side_effect=AssertionError('No import')):
                result = cli.doctor(config)
            self.assertTrue(result['config_ok'])
            self.assertEqual(result['transcript_roots_present'], 1)
            self.assertFalse((root / 'state').exists())

    def test_init_never_overwrites_and_never_defaults_to_home_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['init', '--config', str(path)]), 0)
                self.assertEqual(cli.main(['init', '--config', str(path)]), 1)
            config = json.loads(path.read_text())
            self.assertEqual(config['machines'], [])
            self.assertEqual(config['local']['repo_roots'], [])

    def test_empty_sources_format_brief_an_honest_local_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = daybook.run({'runtime_dir': tmp}, Boundary({}), None, no_model=True)
            text = Path(result['path']).read_text()
            self.assertIn('Nothing recorded', text)
            self.assertEqual(result['calls'], 0)
