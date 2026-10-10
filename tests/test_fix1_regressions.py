# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Synthetic stranger-report regressions; no live inputs or provider calls."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from second_brain import cli, daybook, localgit
from second_brain.model import CLIModel
from second_brain.storage import read
from tests.daybook_fixtures import claude, codex, save
from tests.test_daybook_run import RunFixture


class Fix1Tests(RunFixture):
    def test_false_handoff_surfaces_same_session_failures_and_open_request(self):
        save(self.path, [claude('Finish the auth migration and run the tests.'),
            claude([{'type': 'tool_result', 'content': '3 failed, 39 passed in 0.2s'}], at='2026-10-07T14:00:00Z'),
            claude('I finished the auth migration. All 42 tests pass.', role='assistant', at='2026-10-07T15:00:00Z')])
        with patch('second_brain.daybook.localgit.collect', return_value=([], {'demo': {
                'worktrees': [{'label': '/synthetic/demo', 'branch': 'feature', 'modified': 0, 'untracked': 1}],
                'branches': [], 'repo_available': True, 'has_remote': False}}, {})):
            self.run_book(no_model=True)
            self.assertEqual(self.run_book(no_model=True)['status'], 'unchanged')
        artifact = self.artifact()
        text = artifact['markdown']
        self.assertIn('claims all tests pass', text)
        self.assertIn('3 failed', text)
        self.assertIn('repo does not show', text)
        start = artifact['states']['demo']['state']['start_here']
        self.assertIn('Finish the auth migration and run the tests', start['step'])
        self.assertIn('failing', start['why'])
        self.assertEqual(artifact['facts']['demo']['yesterday']['owner_messages_total'], 1)
        with patch('second_brain.daybook.localgit.collect', return_value=([], {'demo': {
                'worktrees': [], 'branches': [], 'repo_available': True}}, {})):
            self.run_book(no_model=False)
        self.assertIn('claims all tests pass', self.artifact()['markdown'])
        self.assertIn('Finish the auth migration', self.artifact()['states']['demo']['state']['start_here']['step'])

    def test_latest_passing_result_and_other_session_failure_do_not_contradict(self):
        save(self.path, [claude(), claude([{'type': 'tool_result', 'content': '3 failed, 39 passed'}], at='2026-10-07T14:00:00Z'),
            claude([{'type': 'tool_result', 'content': '42 passed in 0.2s'}], at='2026-10-07T14:30:00Z'),
            claude('All 42 tests pass.', role='assistant', at='2026-10-07T15:00:00Z')])
        save(self.root / 'claude/other.jsonl', [claude([{'type': 'tool_result', 'content': '9 failed, 2 passed'}])])
        self.run_book(no_model=True)
        self.assertNotIn('claims all tests pass', self.artifact()['markdown'])

    def test_codex_tool_failure_is_evidence_not_owner_work(self):
        self.config['machines'][0]['codex'] = [str(self.root / 'codex')]
        save(self.root / 'codex/a.jsonl', [
            {'type': 'session_meta', 'payload': {'cwd': '/synthetic/user/projects/demo'}},
            codex('Run the synthetic tests.'),
            {'type': 'response_item', 'timestamp': '2026-10-07T14:00:00Z', 'payload': {
                'type': 'function_call_output', 'output': '3 failed, 39 passed'}},
            codex('All tests pass.', role='assistant', at='2026-10-07T15:00:00Z')])
        self.run_book(no_model=True)
        self.assertIn('claims all tests pass', self.artifact()['markdown'])

    def test_session_worktree_uses_local_checked_out_branch(self):
        with patch('second_brain.daybook.localgit.collect', return_value=([], {'demo': {
                'worktrees': [{'label': '/synthetic/user/projects/demo', 'branch': 'feature/retry'}],
                'branches': []}}, {})):
            self.run_book(no_model=True)
        self.assertEqual(self.artifact()['facts']['demo']['yesterday']['worktrees'][0]['branch'], 'feature/retry')

    def test_clean_no_upstream_or_no_remote_does_not_suggest_push(self):
        for standing in ({'branches_without_upstream': [{'name': 'main'}], 'has_remote': True},
                         {'unpushed_branches': [{'name': 'feature', 'ahead': 2}], 'has_remote': False}):
            self.assertNotIn('Push', daybook.fallback_start({'standing': standing})['step'])
        self.assertIn('Push', daybook.fallback_start({'standing': {
            'unpushed_branches': [{'name': 'feature', 'ahead': 2}], 'has_remote': True}})['step'])

    def test_missing_cli_never_consumes_daily_slots(self):
        self.config.update(model='claude', executable_path=[], executables={})
        model = CLIModel(self.boundary, self.config)
        result = daybook.run(self.config, self.boundary, model, now=self.now)
        self.assertEqual(result['calls'], 0)
        self.assertEqual(read(self.root / 'runtime/calls.json', {}), {})
        self.assertIn('claude CLI is unavailable', self.artifact()['markdown'])

    def test_missing_cli_can_recover_without_spending_a_retry(self):
        self.config.update(model='claude', executable_path=[], executables={})
        model = CLIModel(self.boundary, self.config)
        daybook.run(self.config, self.boundary, model, now=self.now)
        with patch.object(model, 'preflight'), patch.object(model, 'summarize', side_effect=self.model.summarize):
            result = daybook.run(self.config, self.boundary, model, now=self.now)
        self.assertEqual(result['calls'], 2)
        self.assertTrue(all(v['attempts'] == 1 for v in read(self.root / 'runtime/calls.json').values()))

    def test_windows_refuses_before_loading_fcntl_for_module_and_console(self):
        script = '''import sys
sys.platform = 'win32'
class RejectFcntl:
    def find_spec(self, fullname, *args):
        if fullname == 'fcntl':
            raise AssertionError('fcntl imported before platform refusal')
sys.meta_path.insert(0, RejectFcntl())
'''
        for invocation in ("import runpy; runpy.run_module('second_brain', run_name='__main__')",
                           'from second_brain.cli import main; sys.exit(main())'):
            for command in ('demo', 'doctor'):
                result = subprocess.run([sys.executable, '-c', script + invocation, command], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn('daybook supports Linux and macOS; Windows is not supported yet', result.stdout)
                self.assertNotIn('Traceback', result.stderr)

    def test_errors_explain_safe_action_without_echoing_private_input(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(['demo', '--out', str(self.root)]), 1)
        self.assertIn('marked synthetic', output.getvalue())
        self.assertNotIn(str(self.root), output.getvalue())
        from second_brain.storage import write
        write(self.root / 'config.json', {'runtime_dir': str(self.root / 'state')})
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(['brief', '--config', str(self.root / 'config.json'), '--model', 'claude']), 1)
        self.assertIn('--allow-network', output.getvalue())

    def test_symlinked_demo_and_nonempty_formatting(self):
        from second_brain.demo import generate, DEMO_NOW
        target = self.root / 'target'
        target.mkdir()
        link = self.root / 'link'
        link.symlink_to(target, target_is_directory=True)
        config = generate(link / 'demo')
        self.assertTrue(Path(config['runtime_dir']).is_relative_to((link / 'demo').resolve()))
        # Use real local reads for the invented repo rather than RunFixture's adapters.
        with patch('second_brain.daybook.localgit.discover', wraps=self._real_discover), \
             patch('second_brain.daybook.localgit.collect', wraps=self._real_collect):
            result = daybook.run(config, self.boundary, None, now=DEMO_NOW, no_model=True)
        text = Path(result['path']).read_text()
        self.assertNotIn(' ()', text)
        self.assertNotIn(' —  (', text)
        self.assertIn('on 1 branches', text)
        demo_log = (target / 'demo/repos/sample-app/.git/logs/HEAD').read_text()
        self.assertNotIn('Release Export', demo_log)
        self.assertIn('Demo User <demo-user>', demo_log)

    _real_discover = staticmethod(localgit.discover)
    _real_collect = staticmethod(localgit.collect)
