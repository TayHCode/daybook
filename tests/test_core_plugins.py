"""Offline extension hooks and user CLI adapters use synthetic inputs only."""
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from tests.daybook_fixtures import Case
from second_brain import daybook
from second_brain.model import CLIModel


class CorePluginTests(Case):
    def test_hooks_are_scrubbed_and_do_not_enter_model_prompt(self):
        module = ModuleType('synthetic_plugin')
        calls = []
        class Plugin:
            api_version = 1
            def __init__(self, options):
                pass
            def sources(self, context):
                calls.append(context['phase'])
                return {'data': {'note': 'SYNTHETIC_PRIVATE'}}
            def sections(self, context, artifact):
                self.asserted = context['data']['note'] == 'generic'
                if not self.asserted:
                    raise AssertionError('Boundary was not applied')
                return ['## Invented extension\nSYNTHETIC_PRIVATE']
            def postprocess(self, context, artifact):
                artifact['extra'] = 'SYNTHETIC_PRIVATE'
                return artifact
        module.Plugin = Plugin
        config = {**self.config, 'plugins': [{'module': 'synthetic_plugin:Plugin'}]}
        with patch.dict(sys.modules, {'synthetic_plugin': module}):
            first = daybook.run(config, self.boundary, None, no_model=True, now=self.now)
            artifact = json.loads(Path(first['path']).with_suffix('.json').read_text())
            self.assertEqual(artifact['extra'], 'generic')
            self.assertIn('## Invented extension\ngeneric', artifact['markdown'])
            self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(artifact))
            second = daybook.run(config, self.boundary, None, no_model=True, now=self.now)
        self.assertEqual(second['status'], 'unchanged')
        self.assertEqual(calls, ['discover', 'evidence', 'discover', 'evidence'])

    def test_fake_claude_and_codex_cli_parse_json_without_real_calls(self):
        for selected in ('claude', 'codex'):
            with self.subTest(cli=selected):
                config = {**self.config, 'model': selected, '_allow_network': True,
                          'executables': {selected: self.config['executables']['git']}}
                adapter = CLIModel(self.boundary, config)
                seen = []
                def fake(argv, **kwargs):
                    seen.append(argv)
                    self.assertIn('generic', kwargs['input'])
                    self.assertTrue(Path(kwargs['cwd']).is_relative_to(self.root))
                    if selected == 'codex':
                        index = argv.index('--output-last-message') + 1
                        Path(argv[index]).write_text('{"headline":"Invented"}')
                        return SimpleNamespace(returncode=0, stdout='')
                    return SimpleNamespace(returncode=0, stdout=json.dumps({'result':'{"headline":"Invented"}', 'usage':{}}))
                with patch('second_brain.model.subprocess.run', side_effect=fake):
                    value, _ = adapter.summarize('SYNTHETIC_PRIVATE')
                self.assertEqual(value, {'headline': 'Invented'})
                self.assertEqual(len(seen), 1)
                if selected == 'codex':
                    self.assertIn('features.shell_tool=false', seen[0])
                    self.assertIn('--ignore-user-config', seen[0])
                else:
                    self.assertEqual(seen[0][seen[0].index('--tools') + 1], '')
