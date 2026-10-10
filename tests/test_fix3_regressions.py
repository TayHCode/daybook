# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Synthetic cold-state, checkout attribution and platform regression probes."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import random
import subprocess
from unittest.mock import patch

from second_brain import cli, daybook, sessions
from second_brain.demo import generate
from second_brain.storage import read, write
from tests.daybook_fixtures import claude, codex, save
from tests.test_daybook_run import RunFixture
from tests import test_fix2_regressions


class DiagnosticsTests(RunFixture):
    def test_missing_root_keeps_configured_symlink_and_relative_spelling(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        for spelling in (str(alias / 'missing'), './missing-repo', '~/missing-repo'):
            with self.subTest(spelling=spelling):
                config = {**self.config, 'local': {**self.config['local'], 'repo_roots': [spelling]}}
                self.assertEqual(cli.doctor(config)['missing_repo_roots'], [spelling])

    def test_init_preserves_repository_spelling_for_doctor(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        missing = str(alias / 'missing')
        path = self.root / 'new-config.json'
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['init', '--config', str(path), '--repos', missing]), 0)
        self.assertEqual(read(path)['local']['repo_roots'], [missing])
        self.assertEqual(cli.doctor(read(path))['missing_repo_roots'], [missing])


class CheckoutTests(test_fix2_regressions.RepositoryTests):
    def commit(self, path, subject):
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'Release Export', 'GIT_AUTHOR_EMAIL': 'release-export',
               'GIT_COMMITTER_NAME': 'Release Export', 'GIT_COMMITTER_EMAIL': 'release-export',
               'GIT_AUTHOR_DATE': '2026-10-07T15:00:00Z', 'GIT_COMMITTER_DATE': '2026-10-07T15:00:00Z'}
        subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-C', str(path), 'commit',
                        '--allow-empty', '-m', subject, '-m',
                        'Co-Authored-By: Claude Sonnet 5.5 <noreply' + chr(64) + 'anthropic.com>'],
                       env=env, check=True, capture_output=True)

    def test_commit_only_worktree_is_listed_without_session(self):
        self.make_repo()
        self.commit(self.linked, 'Refactor parser')
        self.config['local']['repo_roots'] = [str(self.repo)]
        result = daybook.run(self.config, self.boundary, None, now=self.now, no_model=True)
        artifact = read(Path(result['path']).with_suffix('.json'))
        facts = artifact['facts']['lantern-api']['yesterday']
        self.assertEqual(facts['sessions_total'], 0)
        self.assertIn(str(self.linked.resolve()), {w['label'] for w in facts['worktrees']})
        self.assertIn('feature/retry', {w['branch'] for w in facts['worktrees']})

    def test_detached_commit_in_main_and_linked_checkout_is_collected_once(self):
        self.make_repo()
        for path in (self.repo, self.linked):
            subprocess.run(['git', '-C', str(path), 'checkout', '--detach'], check=True, capture_output=True)
            self.commit(path, 'Detached ' + path.name)
        self.config['local']['repo_roots'] = [str(self.repo)]
        result = daybook.run(self.config, self.boundary, None, now=self.now, no_model=True)
        artifact = read(Path(result['path']).with_suffix('.json'))
        commits = artifact['facts']['lantern-api']['yesterday']['commits']
        self.assertEqual(len(commits), 2)
        self.assertTrue(all('detached HEAD' in c['branches'] for c in commits))
        self.assertIn('detached HEAD', artifact['markdown'])


class RoutingTests(RunFixture):
    def test_claude_and_codex_messages_follow_actual_checkout_within_one_file(self):
        for tool, make in (('claude', claude), ('codex', codex)):
            source = self.root / tool / 'cross.jsonl'
            rows = [make('First checkout request', uuid='first') if tool == 'claude' else
                    {'type': 'session_meta', 'payload': {'id': 'session', 'cwd': '/synthetic/user/projects/demo'}},
                    make('Other checkout 🌱 café 東京', cwd='/synthetic/user/projects/other',
                         at='2026-10-07T14:00:00Z', **{'uuid' if tool == 'claude' else 'id': 'other'}),
                    make('Other checkout handoff', role='assistant', cwd='/synthetic/user/projects/other',
                         at='2026-10-07T15:00:00Z')]
            save(source, rows)
            config = {**self.config, 'runtime_dir': str(self.root / ('state-' + tool)),
                      'machines': [{'name': 'machine-a', tool: [str(source.parent)]}]}
            result = daybook.run(config, self.boundary, None, now=self.now, no_model=True)
            artifact = read(Path(result['path']).with_suffix('.json'))
            self.assertEqual(artifact['facts']['other']['yesterday']['owner_messages_total'], 1)
            self.assertIn('Other checkout 🌱 café 東京', artifact['states']['other']['state']['start_here']['step'])
            self.assertNotIn('Other checkout', json.dumps(artifact['states'].get('demo', {})))

    def test_cwd_change_metadata_reroutes_codex_tool_results_and_inherited_turns(self):
        path = self.root / 'codex/cross.jsonl'
        save(path, [{'type': 'session_meta', 'payload': {'cwd': '/synthetic/user/projects/demo'}},
                    {'type': 'turn_context', 'payload': {'cwd': '/synthetic/user/projects/other'}},
                    codex('Check the other checkout', id='owner'),
                    {'type': 'response_item', 'timestamp': '2026-10-07T14:00:00Z',
                     'payload': {'type': 'function_call_output', 'output': '2 failed, 10 passed'}},
                    codex('All tests pass.', role='assistant', at='2026-10-07T15:00:00Z', id='handoff')])
        config = {**self.config, 'machines': [{'name': 'machine-a', 'codex': [str(path.parent)]}]}
        result = daybook.run(config, self.boundary, None, now=self.now, no_model=True)
        artifact = read(Path(result['path']).with_suffix('.json'))
        self.assertEqual(artifact['facts']['other']['yesterday']['owner_messages_total'], 1)
        self.assertTrue(artifact['facts']['other']['contradictions'])

    def test_older_handoff_contradiction_survives_newer_neutral_handoff(self):
        save(self.path, [claude('Fix tests'), claude('All tests pass.', role='assistant', uuid='claim'),
                         claude(role='user', message={'content': [{'type': 'tool_result', 'content': '3 failed, 39 passed'}]})])
        save(self.root / 'claude/later.jsonl', [claude('Documentation is ready.', role='assistant',
                                                     at='2026-10-07T18:00:00Z')])
        result = self.run_book(no_model=True)
        artifact = read(Path(result['path']).with_suffix('.json'))
        self.assertTrue(artifact['facts']['demo']['contradictions'])
        self.assertIn('reports 3 failed', artifact['markdown'])

    def test_every_handoff_is_checked_even_before_last_two_retained_messages(self):
        save(self.path, [claude('Fix tests'), claude('All tests pass.', role='assistant', uuid='claim'),
                         claude(role='user', message={'content': [{'type': 'tool_result', 'content': '3 failed, 39 passed'}]}),
                         *[claude(f'Neutral update {i}', role='assistant', at=f'2026-10-07T1{i+4}:00:00Z') for i in range(3)]])
        self.run_book(no_model=True)
        self.assertIn('reports 3 failed', self.artifact()['markdown'])


class ColdStateTests(RunFixture):
    def test_fifty_fresh_states_with_shuffled_enumeration_and_equal_timestamps(self):
        original_rglob = Path.rglob
        for iteration in range(50):
            source = self.root / f'source-{iteration}'
            owner = claude('Refactor the parser 🌱 東京', uuid='owner')
            handoff = claude('Parser refactor is complete.', role='assistant', uuid='handoff')
            for name in ('a.jsonl', 'nested/b.jsonl', 'z.jsonl'):
                save(source / name, [owner, handoff])
                os.utime(source / name, (1791378000, 1791378000))
            config = {**self.config, 'runtime_dir': str(self.root / f'state-{iteration}'),
                      'machines': [{'name': 'machine-a', 'claude': [str(source)]}]}
            rng = random.Random(iteration)
            def shuffled(path, pattern, *args, **kwargs):
                values = list(original_rglob(path, pattern, *args, **kwargs))
                rng.shuffle(values)
                return iter(values)
            with patch.object(Path, 'rglob', shuffled):
                first = daybook.run(config, self.boundary, None, now=self.now, no_model=True)
                before = Path(first['path']).read_bytes()
                second = daybook.run(config, self.boundary, None, now=self.now, no_model=True)
            self.assertEqual(Path(second['path']).read_bytes(), before)
            self.assertIn(b'Parser refactor is complete.', before)
            self.assertNotIn(b'Unordered native-message conflicts yesterday:', before)

    def test_retained_mirror_handoff_survives_canonical_copy_pruning(self):
        owner = claude('Refactor parser', uuid='owner')
        handoff = claude('Parser refactor is complete.', role='assistant',
                         at='2026-10-07T18:00:00Z', uuid='handoff')
        # A synced file need not be in timestamp order. Its last two physical
        # assistant rows are older than a handoff retained by an equivalent copy.
        save(self.path, [owner, handoff,
                         claude('Earlier update one', role='assistant', at='2026-10-07T15:00:00Z'),
                         claude('Earlier update two', role='assistant', at='2026-10-07T16:00:00Z')])
        save(self.root / 'claude/z-mirror.jsonl', [owner, handoff])
        first = self.run_book(no_model=True)
        before = Path(first['path']).read_bytes()
        self.assertIn(b'Parser refactor is complete.', before)
        self.assertEqual(self.run_book(no_model=True)['status'], 'unchanged')
        self.assertEqual(Path(first['path']).read_bytes(), before)

    def test_unordered_conflict_does_not_create_revision_provenance_on_rescan(self):
        owner = claude('Refactor parser', uuid='owner')
        alpha = claude('Parser refactor is pending.', role='assistant', uuid='handoff')
        bravo = claude('Parser refactor is complete.', role='assistant', uuid='handoff')
        save(self.path, [owner, alpha])
        save(self.root / 'claude/z-copy.jsonl', [owner, bravo])
        first = self.run_book(no_model=True)
        before = Path(first['path']).read_bytes()
        # A synced replay must not promote a cold arbitrary version into an edit.
        frozen = self.path.stat().st_mtime_ns
        with self.path.open('a') as handle:
            handle.write(json.dumps(alpha) + '\n')
        os.utime(self.path, ns=(frozen, frozen))
        second = self.run_book(no_model=True)
        self.assertEqual(self.artifact()['coverage']['sessions']['machine-a']['claude']['version_conflicts_yesterday'], 1)
        self.assertEqual(Path(second['path']).read_bytes(), before)
        self.assertIn(b'Unordered native-message conflicts yesterday: 1.', before)

    def test_rewrite_and_late_stale_copy_preserve_latest_handoff(self):
        owner = claude('Refactor parser', uuid='owner')
        alpha = claude('Parser refactor is pending.', role='assistant', uuid='handoff')
        bravo = claude('Parser refactor is complete.', role='assistant', uuid='handoff')
        save(self.path, [owner, alpha])
        self.run_book(no_model=True)
        save(self.path, [owner, bravo])
        self.run_book(no_model=True)
        save(self.root / 'claude/mirror.jsonl', [owner, alpha])
        self.run_book(no_model=True)
        self.assertIn('Parser refactor is complete.', self.artifact()['markdown'])
        self.assertNotIn('Unordered native-message conflicts yesterday:', self.artifact()['markdown'])


class DemoTests(RunFixture):
    def test_new_demo_commit_has_only_neutral_synthetic_author_and_subject(self):
        root = self.root / 'demo'
        generate(root)
        output = subprocess.check_output(['git', '-C', str(root / 'repos/sample-app'),
                                          'log', '-1', '--format=%an%n%ae%n%B'], text=True)
        self.assertTrue(output.startswith('Demo User\ndemo-user\n'))
        self.assertNotIn('Co-Authored-By:', output)
