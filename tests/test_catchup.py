# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Synthetic carry-over, gaps, and crash recovery; no real repositories or sessions."""
from datetime import datetime, timezone
import os
from pathlib import Path
from unittest.mock import patch
from second_brain import catchup, daybook
from second_brain.dayconfig import configure, window
from second_brain.storage import read, write
from tests.daybook_fixtures import Case, claude, save


class CatchupTests(Case):
    def setUp(self):
        super().setUp()
        self.root_state = self.root / 'runtime'
        self.config['timezone'] = 'UTC'
        self.config['_allow_network'] = False
        self.now = datetime(2026, 10, 12, 9, tzinfo=timezone.utc)
        self.config['machines'] = []
        self.config['local']['repo_roots'] = []
        self.config['executables'] = {}

    def seed(self, date='2026-10-10', evidence_date='2026-10-09', partial=False):
        value = {'date': date, 'evidence_date': evidence_date, 'partial': partial,
                 'states': {'demo': {'evidence_date': evidence_date, 'state': {
                     'open': ['Document parser errors.'], 'deferred': ['Review optional telemetry.'],
                     'start_here': {'step': 'Run the parser regression.', 'where': {}, 'sources': []}}}}}
        write(self.root_state / 'briefs' / (date + '.json'), value)
        return value

    def book(self, date='yesterday', **kwargs):
        result = daybook.run(self.config, self.boundary, None, date, now=self.now, no_model=True, **kwargs)
        return result, read(Path(result['path']).with_suffix('.json'))

    def test_config_default_and_bounds(self):
        self.assertEqual(configure({'runtime_dir': 'unused'})['catchup_days'], 4)
        for value in (0, 15, True, 4.5, '4'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'catchup_days'):
                configure({'runtime_dir': 'unused', 'catchup_days': value})
        for value in (1, 14):
            self.assertEqual(configure({'runtime_dir': 'unused', 'catchup_days': value})['catchup_days'], value)

    def test_friday_to_monday_and_missed_day_keep_all_kinds(self):
        self.seed()
        _, artifact = self.book()
        rows = artifact['catchup']['carried']
        self.assertEqual({r['text'] for r in rows}, {'Document parser errors.', 'Review optional telemetry.', 'Run the parser regression.'})
        self.assertTrue(all(r['first_seen'] == '2026-10-09' and r['carried_days'] == 3 for r in rows))
        self.assertIn('first seen Fri, carried 3 days', artifact['markdown'])
        self.assertLess(artifact['markdown'].index("## Check you didn't drop anything"), artifact['markdown'].index('## Yesterday'))
        self.now = datetime(2026, 10, 14, 9, tzinfo=timezone.utc)
        _, later = self.book()
        self.assertEqual({r['id'] for r in rows}, {r['id'] for r in later['catchup']['stale']})
        self.assertIn('Stale (not touched for 5 days)', later['markdown'])

    def test_deleted_newest_brief_does_not_delete_carry_state(self):
        self.seed()
        result, artifact = self.book()
        Path(result['path']).unlink()
        Path(result['path']).with_suffix('.json').unlink()
        self.now = datetime(2026, 10, 13, 9, tzinfo=timezone.utc)
        _, later = self.book()
        self.assertEqual({r['id'] for r in artifact['catchup']['carried']},
                         {r['id'] for r in later['catchup']['carried'] + later['catchup']['stale']})

    def test_failed_write_and_partial_collection_preserve_items(self):
        self.seed()
        original = daybook.write
        def fail_brief(path, value):
            if Path(path).parent.name == 'briefs':
                raise OSError('Synthetic write interruption')
            return original(path, value)
        with patch('second_brain.daybook.write', side_effect=fail_brief), self.assertRaises(OSError):
            self.book()
        self.assertTrue((self.root_state / 'state/catchup.json').is_file())
        with patch('second_brain.daybook.localgit.collect', side_effect=OSError('Synthetic unavailable')):
            result, artifact = self.book()
        self.assertTrue(result['partial'])
        self.assertEqual(len(artifact['catchup']['carried']), 3)

    def test_monday_widens_since_friday_brief_and_keeps_day_labels(self):
        self.seed('2026-10-09', '2026-10-08')
        self.config['machines'] = [{'name': 'machine-a', 'claude': [str(self.root / 'claude')]}]
        save(self.root / 'claude/a.jsonl', [claude('Friday task?', at='2026-10-09T18:00:00Z'),
             claude('Saturday task?', at='2026-10-10T18:00:00Z'), claude('Sunday task?', at='2026-10-11T18:00:00Z')])
        _, artifact = self.book()
        self.assertEqual(artifact['window']['start'], '2026-10-09T00:00:00+00:00')
        self.assertEqual(artifact['facts']['demo']['yesterday']['owner_messages_total'], 3)
        self.assertIn('Fri 2026-10-09', artifact['markdown'])
        self.assertIn('Sat 2026-10-10', artifact['markdown'])
        self.assertIn('Sun 2026-10-11', artifact['markdown'])
        self.assertEqual(artifact['catchup']['missing_days'], ['2026-10-08', '2026-10-10', '2026-10-11'])
        self.assertIn('no brief since Fri', artifact['markdown'])
        explicit = catchup.widen(window(self.config, '2026-10-11', self.now), '2026-10-11', [self.seed()])
        self.assertEqual(explicit['start'].date().isoformat(), '2026-10-11')

    def test_no_model_network_and_private_permissions_and_rerun(self):
        self.seed()
        with patch('second_brain.model.CLIModel.summarize', side_effect=AssertionError('No model')), \
             patch('socket.socket.connect', side_effect=AssertionError('No network')):
            result, artifact = self.book()
            before = (self.root_state / 'state/catchup.json').read_bytes()
            rerun, _ = self.book()
        self.assertEqual((result['calls'], rerun['status'], rerun['calls']), (0, 'unchanged', 0))
        self.assertEqual((self.root_state / 'state/catchup.json').read_bytes(), before)
        for path in [self.root_state, self.root_state / 'state', self.root_state / 'briefs']:
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        for path in [self.root_state / 'state/catchup.json', Path(result['path']), Path(result['path']).with_suffix('.json')]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_explicit_matching_completion_resolved_once_and_generic_done_not_enough(self):
        self.seed()
        _, before = self.book()
        self.now = datetime(2026, 10, 13, 9, tzinfo=timezone.utc)
        self.config['machines'] = [{'name': 'machine-a', 'claude': [str(self.root / 'claude')]}]
        save(self.root / 'claude/a.jsonl', [claude('Done.', at='2026-10-12T12:00:00Z')])
        _, generic = self.book()
        self.assertFalse(generic['catchup']['resolved'])
        save(self.root / 'claude/a.jsonl', [claude('Done: Document parser errors.', at='2026-10-12T12:00:00Z')])
        _, resolved = self.book()
        self.assertEqual([r['text'] for r in resolved['catchup']['resolved']], ['Document parser errors.'])
        self.assertIn('Resolved since', resolved['markdown'])
        self.assertNotIn('Document parser errors.', [r['text'] for r in resolved['catchup']['carried'] + resolved['catchup']['stale']])
        self.now = datetime(2026, 10, 14, 9, tzinfo=timezone.utc)
        _, later = self.book()
        self.assertFalse(later['catchup']['resolved'])
        self.assertNotIn('Document parser errors.', [r['text'] for r in later['catchup']['carried'] + later['catchup']['stale']])

    def test_unfinished_evidence_uncertainty_and_fresh_completion(self):
        win = window(self.config, 'yesterday', self.now)
        local = {'demo': {'repo_available': True, 'has_remote': True, 'worktrees': [
            {'label': 'synthetic-tree', 'branch': 'feature', 'modified': 1, 'untracked': 0, 'head': 'old', 'status_checked': True}],
            'branches': [{'name': 'feature', 'head': 'old', 'committed_at': '2026-10-09T15:00:00Z', 'upstream': 'origin/feature', 'ahead': 2},
                         {'name': 'solo', 'head': 'solo-head', 'committed_at': '2026-10-09T16:00:00Z', 'upstream': '', 'ahead': 0}]}}
        evidence = [{'id': 'question', 'project': 'demo', 'session': 's', 'sequence': 1, 'kind': 'owner_message',
                     'event_at': '2026-10-09T18:00:00Z', 'text': 'Which parser cases need tests?', 'verification': 'reported'}]
        ledger, report = catchup.reconcile({}, [], {}, local, {}, evidence, win, self.config)
        kinds = {r['kind'] for r in report['carried']}
        self.assertTrue({'worktree', 'ahead', 'no_upstream', 'branch', 'session'} <= kinds)
        solo = next(r for r in report['carried'] if r['kind'] == 'no_upstream')
        self.assertIn('publication unknown', solo['text'])
        unknown, failed = catchup.reconcile(ledger, [], {}, {}, {}, [], win, self.config)
        self.assertEqual(len(failed['carried']), len(report['carried']))
        local['demo']['worktrees'][0].update(modified=0, head='new')
        local['demo']['branches'][0].update(ahead=0, head='new')
        _, clean = catchup.reconcile(ledger, [], {}, local, {}, [], win, self.config, fresh_local=True)
        self.assertEqual({r['kind'] for r in clean['resolved']}, {'worktree', 'ahead'})
        self.assertIn('no_upstream', {r['kind'] for r in clean['carried']})

    def test_old_history_survives_short_horizon_and_duplicate_text_keeps_id(self):
        self.seed('2026-09-10', '2026-09-09')
        self.config['catchup_days'] = 1
        _, artifact = self.book()
        self.assertEqual(len(artifact['catchup']['stale']), 3)
        ids = {r['id'] for r in artifact['catchup']['stale']}
        self.seed()
        _, again = self.book()
        self.assertEqual(ids, {r['id'] for r in again['catchup']['stale']})

    def test_conflicted_session_completion_and_unrelated_merge_do_not_resolve(self):
        win = window(self.config, 'yesterday', self.now)
        history = [self.seed()]
        event = {'id': 'done', 'project': 'demo', 'kind': 'closing_handoff', 'session': 's', 'sequence': 1,
                 'event_at': '2026-10-11T12:00:00Z', 'text': 'Done: Document parser errors.', 'verification': 'conflict'}
        _, result = catchup.reconcile({}, history, {}, {}, {}, [event], win, self.config)
        self.assertFalse(result['resolved'])
        event.update(kind='local_commit', verification='local_git', text='Completed unrelated change')
        _, result = catchup.reconcile({}, history, {}, {}, {}, [event], win, self.config)
        self.assertFalse(result['resolved'])

    def test_saved_model_item_survives_interrupt_before_brief_and_plain_mode(self):
        self.seed()
        write(self.root_state / 'state/demo.json', {'evidence_date': '2026-10-11', 'state': {
            'open': ['Check parser timeout handling.'], 'deferred': [], 'start_here': {'step': ''}}})
        _, artifact = self.book()
        self.assertIn('Check parser timeout handling.', [r['text'] for r in artifact['catchup']['carried']])

    def test_clean_worktree_same_head_and_cached_ahead_zero_cannot_resolve(self):
        win = window(self.config, 'yesterday', self.now)
        local = {'demo': {'repo_available': True, 'worktrees': [
            {'label': 'tree', 'branch': 'feature', 'modified': 1, 'head': 'one', 'status_checked': True}],
            'branches': [{'name': 'feature', 'head': 'one', 'committed_at': '2026-10-09T12:00:00Z', 'upstream': 'origin/feature', 'ahead': 1}]}}
        saved, _ = catchup.reconcile({}, [], {}, local, {}, [], win, self.config)
        local['demo']['worktrees'][0]['modified'] = 0
        local['demo']['branches'][0]['ahead'] = 0
        _, report = catchup.reconcile(saved, [], {}, local, {}, [], win, self.config)
        self.assertFalse(report['resolved'])
        _, report = catchup.reconcile(saved, [], {}, local, {}, [], win, self.config, fresh_local=True)
        self.assertEqual({r['kind'] for r in report['resolved']}, {'ahead'})

    def test_catchup_uses_uncapped_local_rows_and_partial_brief_is_a_gap(self):
        self.seed(partial=True)
        local = {'demo': {'repo_available': True, 'worktrees': [
            {'label': 'tree-' + str(i), 'branch': 'feature', 'modified': 1, 'head': 'head'} for i in range(45)]}}
        with patch('second_brain.daybook.localgit.collect', return_value=([], local, {})):
            _, artifact = self.book()
        self.assertEqual(sum(r['kind'] == 'worktree' for r in artifact['catchup']['carried']), 45)
        self.assertIn('2026-10-10', artifact['catchup']['missing_days'])

    def test_cli_fixed_now_and_weekend_demo(self):
        from contextlib import redirect_stdout
        import io
        from second_brain import cli
        destination = self.root / 'demo'
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['demo', '--out', str(destination)]), 0)
        text = output.getvalue()
        self.assertIn("Check you didn't drop anything", text)
        self.assertIn('first seen Fri', text)
        self.assertIn('Resolved since', text)
        self.assertIn('Stale (not touched for 6 days)', text)
        self.assertIn('Missing complete briefs in reminder lookback:', text)
        self.assertIn('Sat 2026-10-10', text)
        self.assertIn('Sun 2026-10-11', text)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['brief', '--config', str(destination / 'config.json'), '--model', 'none',
                                      '--date', 'yesterday', '--now', '2026-10-12T09:00:00Z', '--json']), 0)
        self.assertEqual(__import__('json').loads(output.getvalue())['status'], 'unchanged')
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['brief', '--config', str(destination / 'config.json'), '--now', '2026-10-12']), 1)

    def test_merge_evidence_resolves_only_matching_branch(self):
        win = window(self.config, 'yesterday', self.now)
        local = {'demo': {'repo_available': True, 'branches': [
            {'name': 'feature', 'head': 'head', 'upstream': '', 'committed_at': '2026-10-09T12:00:00Z'}]}}
        saved, _ = catchup.reconcile({}, [], {}, local, {}, [], win, self.config)
        merged = {'id': 'merge', 'project': 'demo', 'kind': 'pull_request', 'event': 'merged',
                  'number': 7, 'head_label': 'other', 'event_at': '2026-10-11T12:00:00Z'}
        _, report = catchup.reconcile(saved, [], {}, {}, {}, [merged], win, self.config)
        self.assertFalse(report['resolved'])
        merged['head_label'] = 'feature'
        _, report = catchup.reconcile(saved, [], {}, {}, {}, [merged], win, self.config)
        self.assertEqual({r['kind'] for r in report['resolved']}, {'branch', 'no_upstream'})

    def test_fourteen_day_question_survives_shorter_transcript_retention(self):
        self.config.update(catchup_days=14, active_days=1)
        self.config['machines'] = [{'name': 'machine-a', 'claude': [str(self.root / 'claude')]}]
        save(self.root / 'claude/a.jsonl', [claude('Which parser edge case is pending?', at='2026-09-29T18:00:00Z')])
        _, artifact = self.book()
        self.assertTrue(any(r['kind'] == 'session' for r in artifact['catchup']['carried']))

    def test_interrupted_markdown_write_repaired_on_identical_retry(self):
        self.seed()
        original = daybook.write
        def fail_markdown(path, value):
            if Path(path).suffix == '.md':
                raise OSError('Synthetic Markdown interruption')
            return original(path, value)
        with patch('second_brain.daybook.write', side_effect=fail_markdown), self.assertRaises(OSError):
            self.book()
        result, artifact = self.book()
        self.assertTrue(Path(result['path']).is_file())
        self.assertEqual(len(artifact['catchup']['carried']), 3)

    def test_reused_branch_and_worktree_create_new_reminders_after_resolution(self):
        win = window(self.config, 'yesterday', self.now)
        local = {'demo': {'repo_available': True, 'worktrees': [
            {'label': 'tree', 'branch': 'feature', 'modified': 1, 'head': 'old', 'status_checked': True}],
            'branches': [{'name': 'feature', 'head': 'old', 'upstream': 'origin/feature', 'ahead': 1, 'committed_at': '2026-10-09T12:00:00Z'}]}}
        saved, first = catchup.reconcile({}, [], {}, local, {}, [], win, self.config)
        local['demo']['worktrees'][0].update(modified=0, head='new')
        local['demo']['branches'][0].update(head='new', ahead=0)
        saved, resolved = catchup.reconcile(saved, [], {}, local, {}, [], win, self.config, fresh_local=True)
        self.assertEqual({r['kind'] for r in resolved['resolved']}, {'worktree', 'ahead'})
        local['demo']['worktrees'][0].update(modified=1, head='new')
        local['demo']['branches'][0].update(head='newer', ahead=1, committed_at='2026-10-12T16:00:00Z')
        self.now = datetime(2026, 10, 13, 9, tzinfo=timezone.utc)
        later = window(self.config, 'yesterday', self.now)
        _, report = catchup.reconcile(saved, [], {}, local, {}, [], later, self.config, fresh_local=True)
        self.assertTrue({'worktree', 'ahead'} <= {r['kind'] for r in report['carried']})
        old = {r['id'] for r in first['carried'] if r['kind'] in ('worktree', 'ahead')}
        new = {r['id'] for r in report['carried'] if r['kind'] in ('worktree', 'ahead')}
        self.assertTrue(old.isdisjoint(new))
