# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
import fcntl
import io
import json
import os
from collections import Counter
from contextlib import redirect_stderr
from datetime import timedelta
from unittest.mock import patch
from second_brain import daybook
from second_brain.storage import read, write
from tests.daybook_fixtures import Case, Model, claude, save


class RunFixture(Case):
    def setUp(self):
        super().setUp()
        self.path = self.root / 'claude/a.jsonl'
        save(self.path, [claude(), claude('A synthetic handoff', role='assistant')])
        self.model = Model()
        for target, value in [('sources.repos', []), ('sources.collect', ([], {}, {})), ('localgit.discover', []), ('localgit.collect', ([], {}, {}))]:
            p = patch('second_brain.daybook.' + target, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def run_book(self, **kwargs):
        return daybook.run(self.config, self.boundary, self.model, now=self.now, **kwargs)

    def artifact(self):
        return read(self.root / 'runtime/briefs/2026-10-08.json')


class RunTests(RunFixture):
    def test_default_twelve_project_cap_with_fourteen_active_projects(self):
        for i in range(13):
            label = f'fixture-{i:02d}'
            self.config['projects'].append({'id': label, 'aliases': [label]})
            save(self.root / f'claude/{label}.jsonl', [claude(f'Request for {label}', cwd=f'/synthetic/user/projects/{label}')])
        self.assertEqual(self.config['max_project_calls'], 12)
        result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('partial', 13))
        self.assertEqual(len(self.artifact()['active']), 14)
        self.assertEqual(len(self.artifact()['coverage']['deferred_projects']), 2)
        budget_path = self.root / 'runtime/calls.json'
        budget = budget_path.read_bytes()
        self.assertEqual(len(read(budget_path)), 13)
        self.assertTrue(all(slot['attempts'] == 1 for slot in read(budget_path).values()))
        result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('unchanged', 0))
        self.assertEqual(budget_path.read_bytes(), budget)
        self.assertEqual(len(self.artifact()['coverage']['deferred_projects']), 2)

    def test_seven_machines_stale_source_missing_and_recovered(self):
        for i in range(1, 7):
            folder = self.root / f'claude-{i}'
            self.config['machines'].append({'name': f'machine-{i}', 'claude': [str(folder)]})
            at = '2026-10-04T13:00:00Z' if i == 1 else '2026-10-07T13:00:00Z'
            path = folder / 'a.jsonl'
            save(path, [claude(f'Owner request from synthetic machine {i}', at=at)])
            if i == 1:
                stamp = (self.now - timedelta(days=4)).timestamp()
                os.utime(path, (stamp, stamp))
        self.assertEqual(self.run_book()['calls'], 2)
        before = self.artifact()
        cov = before['coverage']['sessions']
        self.assertEqual(len(cov), 7)
        self.assertEqual(cov['machine-1']['claude']['status'], 'ok')
        self.assertEqual(cov['machine-1']['claude']['owner_messages_yesterday'], 0)
        self.assertEqual(before['facts']['demo']['yesterday']['owner_messages_total'], 6)
        self.assertEqual(before['facts']['demo']['standing']['active_days_count'], 2)
        folder = self.root / 'claude-6'
        offline = self.root / 'offline'
        folder.rename(offline)
        result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('partial', 0))
        self.assertEqual(self.artifact()['coverage']['sessions']['machine-6']['claude']['status'], 'source_unavailable')
        offline.rename(folder)
        result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('refreshed', 0))
        self.assertEqual(self.artifact()['states'], before['states'])
        self.assertEqual(self.artifact()['facts']['demo']['yesterday']['owner_messages_total'], 6)

    def test_empty_yesterday_with_earlier_owner_activity_still_format_briefs(self):
        save(self.path, [claude('Earlier work', at='2026-10-04T13:00:00Z')])
        result = self.run_book(no_model=True)
        self.assertEqual((result['status'], result['calls']), ('written', 0))
        self.assertEqual(self.artifact()['active'], ['demo'])
        self.assertEqual(self.artifact()['worked_yesterday'], [])

    def test_same_size_correction_refreshes_once_without_spending_or_rewriting_model(self):
        self.run_book()
        before = self.artifact()
        ledger = (self.root / 'runtime/calls.json').read_bytes()
        stamp = self.path.stat()
        data = self.path.read_bytes().replace(b'Build the synthetic feature', b'Check the synthetic feature')
        self.path.write_bytes(data)
        self.assertEqual(self.path.stat().st_size, stamp.st_size)
        os.utime(self.path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('refreshed', 0))
        self.assertEqual(self.artifact()['states'], before['states'])
        self.assertNotEqual(self.artifact()['mark'], before['mark'])
        self.assertEqual((self.root / 'runtime/calls.json').read_bytes(), ledger)
        cache = read(self.root / 'runtime/cache/sessions/machine-a.claude.json')
        self.assertIn('Check the synthetic feature', [m['text'] for entry in cache.values() for m in entry['messages']])
        self.assertNotIn('Build the synthetic feature', [m['text'] for entry in cache.values() for m in entry['messages']])
        self.assertEqual(read(self.root / 'runtime/cache/sessions/diagnostics.json')['machine-a']['claude']['files_scanned'], 1)
        paths = [self.root / f'runtime/briefs/2026-10-08.{ext}' for ext in ('json', 'md')]
        preserved = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
        self.assertEqual((self.run_book()['status'], len(self.model.calls)), ('unchanged', 2))
        self.assertEqual([(p.read_bytes(), p.stat().st_mtime_ns) for p in paths], preserved)

    def test_new_mirrors_and_same_file_replay_preserve_snapshot_and_credit(self):
        mirror = self.root / 'mirror'
        mirror.mkdir()
        # Both configured machines exist before the first snapshot. The mirror
        # sorts before the original, exercising source-credit stability too.
        self.config['machines'].append({'name': 'machine-0', 'claude': [str(mirror)]})
        self.run_book()
        paths = [self.root / f'runtime/briefs/2026-10-08.{ext}' for ext in ('json', 'md')]
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
        initial_states = self.artifact()['states']
        initial_calls = (self.root / 'runtime/calls.json').read_bytes()
        for index, path in enumerate((mirror / 'copy.jsonl', self.root / 'claude/0-copy.jsonl', self.path), 1):
            with path.open('ab') as handle:
                handle.write(self.path.read_bytes())
            with patch('second_brain.daybook._model_states', side_effect=AssertionError('mirror triggered model')):
                result = self.run_book()
            self.assertEqual((result['status'], result['calls']), ('unchanged', 0))
            self.assertEqual([(p.read_bytes(), p.stat().st_mtime_ns) for p in paths], before)
            self.assertEqual(self.artifact()['states'], initial_states)
            self.assertEqual((self.root / 'runtime/calls.json').read_bytes(), initial_calls)
            cov = read(self.root / 'runtime/cache/sessions/diagnostics.json')
            self.assertEqual(sum(c['duplicate_rows_yesterday'] for tools in cov.values() for c in tools.values()), index * 2)
            self.assertEqual(cov['machine-a']['claude']['owner_messages_yesterday'], 1)
            self.assertEqual(cov['machine-0']['claude']['owner_messages_yesterday'], 0)

    def test_busy_project_compacts_facts_and_samples_sources_within_budget(self):
        self.path.unlink()
        for session in range(20):
            rows = [claude(f'Owner request {session}/{i}: ' + 'synthetic detail ' * 15,
                           at=(self.win['start'] + timedelta(hours=1, minutes=i)).isoformat()) for i in range(80)]
            rows.append(claude(f'Latest handoff for synthetic session {session}', role='assistant', at='2026-10-07T20:00:00Z'))
            save(self.root / f'claude/busy-{session}.jsonl', rows)
        commits = [{'id': f'gh.{i}', 'source': f'synthetic:commit:{i}', 'project': 'demo',
                    'kind': 'commit', 'sha': f'{i:012x}', 'event_at': '2026-10-07T21:00:00Z',
                    'period': 'yesterday', 'verification': 'catalog', 'subject': f'Synthetic change {i}',
                    'text': f'Synthetic change {i}', 'branches': ['feature']} for i in range(5)]
        local = {'demo': {'worktrees': [{'label': '/synthetic/' + 'area/' * 500 + str(i),
                    'branch': 'feature', 'modified': 0, 'untracked': 0} for i in range(30)]}}
        with patch('second_brain.daybook.sources.collect', return_value=(commits, {}, {})), \
             patch('second_brain.daybook.localgit.collect', return_value=([], local, {})):
            result = self.run_book()
            self.assertEqual((result['status'], result['calls']), ('written', 2))
            a = self.artifact()
            self.assertGreater(len(json.dumps(a['facts']['demo'])), 60000)
            prompt = self.model.calls[0]
            self.assertLessEqual(len(prompt), self.config['project_max_chars'])
            data = json.loads(prompt[len(daybook.STATE_INSTRUCTION):])
            sample = data['sampling']
            self.assertEqual(sample['evidence_total'], 1625)
            self.assertGreater(sample['evidence_selected'], 40)
            self.assertEqual(sample['evidence_omitted'], 1625 - len(data['evidence']))
            self.assertEqual(sum(g['records'] for g in sample['counts_by_day_kind']), 1625)
            counts = Counter(e['session'] for e in data['evidence'] if e.get('session'))
            self.assertEqual(len(counts), 20)
            self.assertLessEqual(max(counts.values()), 8)
            self.assertEqual({e['kind'] for e in data['evidence']}, {'owner_message', 'closing_handoff', 'commit'})
            self.assertEqual(len([e for e in data['evidence'] if e['kind'] == 'closing_handoff']), 20)
            self.assertEqual(data['facts']['yesterday']['owner_messages_total'], 1600)
            self.assertIn(f"{sample['evidence_omitted']} omitted", a['markdown'])
            self.assertNotEqual(a['states']['demo']['state']['start_here'].get('source'), 'rule')
            self.assertEqual((self.run_book()['status'], len(self.model.calls)), ('unchanged', 2))

    def test_compaction_is_deterministic_and_handles_oversized_previous_state(self):
        self.run_book(no_model=True)
        facts = self.artifact()['facts']['demo']
        evidence = [{'id': str(i), 'source': f'synthetic:{i}', 'project': 'demo', 'kind': 'owner_message',
                     'session': str(i % 4), 'period': 'yesterday', 'event_at': '2026-10-07T12:00:00Z',
                     'text': f'Owner fixture {i}', 'verification': 'reported'} for i in range(100)]
        ids = daybook.source_ids(evidence)
        kwargs = {'date': '2026-10-07', 'window': {}}
        one = daybook.compact_project_prompt('demo', facts, evidence, {'open': ['long ' * 20000]}, ids, 12000, **kwargs)
        two = daybook.compact_project_prompt('demo', facts, list(reversed(evidence)), {'open': ['long ' * 20000]}, ids, 12000, **kwargs)
        self.assertEqual(one, two)
        self.assertLessEqual(len(one[0]), 12000)
        self.assertTrue(one[2]['previous_state_omitted'])

    def test_agent_copies_do_not_activate_project_or_reach_owner_evidence(self):
        save(self.path, [claude('Check synthetic task!', authored_by='agent'), claude(' check SYNTHETIC\n task ', role='assistant')])
        save(self.root / 'claude/other.jsonl', [claude('Build another fixture', cwd='/synthetic/user/projects/other')])
        self.run_book(no_model=True)
        a = self.artifact()
        self.assertNotIn('demo', a['active'])
        self.assertEqual(a['facts']['demo']['yesterday']['owner_messages_total'], 0)
        self.assertIn('1 excluded user rows', a['markdown'])
        self.assertIn('explicit_author: 1', a['markdown'])

    def test_unrelated_live_echo_preserves_owner_activity_and_snapshot(self):
        self.config['projects'].append({'id': 'other', 'aliases': ['other']})
        self.run_book()
        paths = [self.root / f'runtime/briefs/2026-10-08.{ext}' for ext in ('json', 'md')]
        before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in paths]
        save(self.root / 'claude/echo.jsonl', [claude('Build the synthetic feature', role='assistant',
             at='2026-10-08T10:00:00Z', cwd='/synthetic/user/projects/other')])
        with patch('second_brain.daybook._model_states', side_effect=AssertionError('reuse shortcut missed')):
            result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('unchanged', 0))
        self.assertEqual([(p.read_bytes(), p.stat().st_mtime_ns) for p in paths], before)
        self.assertEqual(self.artifact()['active'], ['demo'])
        self.assertEqual(self.artifact()['facts']['demo']['yesterday']['owner_messages_total'], 1)

    def test_no_model_sections_start_steps_and_rerun(self):
        result = self.run_book(no_model=True)
        self.assertEqual((result['status'], result['calls']), ('written', 0))
        a = self.artifact()
        self.assertEqual(a['active'], ['demo'])
        self.assertEqual(a['states']['demo']['state']['start_here']['source'], 'rule')
        headings = ['## Yesterday', '## Where each active project stands', '## Start here', '## Coverage']
        self.assertEqual(sorted(headings, key=a['markdown'].index), headings)
        self.assertIn('Latest assistant handoff (unverified): A synthetic handoff', a['markdown'])
        before = (self.root / 'runtime/briefs/2026-10-08.json').stat().st_mtime_ns
        self.assertEqual(self.run_book(no_model=True)['status'], 'unchanged')
        self.assertEqual((self.root / 'runtime/briefs/2026-10-08.json').stat().st_mtime_ns, before)

    def test_format_brief_caps_and_lock(self):
        save(self.path, [claude()])
        for i in range(9):
            save(self.root / ('claude/extra' + str(i) + '.jsonl'), [claude(f'Synthetic session {i}')])
        self.run_book(no_model=True)
        self.assertIn('+4 more', self.artifact()['markdown'])
        with (self.root / 'runtime/daybook.lock').open('r') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_book(no_model=True)['status'], 'locked')

    def test_full_run_unchanged_and_late_refresh(self):
        first = self.run_book()
        self.assertEqual(first['calls'], 2)
        self.assertEqual(first['status'], 'written')
        artifact = self.artifact()
        before = (self.root / 'runtime/briefs/2026-10-08.json').stat().st_mtime_ns
        result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('unchanged', 0))
        self.assertEqual((self.root / 'runtime/briefs/2026-10-08.json').stat().st_mtime_ns, before)
        save(self.path, [claude(), claude('A synthetic handoff', role='assistant'), claude('Late evidence')])
        result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('refreshed', 0))
        self.assertEqual(self.artifact()['states'], artifact['states'])
        self.assertEqual(self.artifact()['facts']['demo']['yesterday']['owner_messages_total'], 2)

        with patch('second_brain.daybook.localgit.collect', return_value=([], {}, {'note': 'Updated coverage'})):
            self.run_book()
        self.assertIn('| machine-a | 1 | 0 |', self.artifact()['markdown'])
        self.assertIn('Files used includes unchanged files reused', self.artifact()['markdown'])

    def test_failed_slot_retry_only_once(self):
        self.model.summarize = lambda prompt: (_ for _ in ()).throw(RuntimeError('private failure text'))
        errors = io.StringIO()
        with redirect_stderr(errors):
            first = self.run_book()
            self.assertEqual((first['status'], first['calls']), ('partial', 2))
            self.assertEqual(self.run_book()['calls'], 0)
            self.assertEqual(self.run_book(retry_failed=True)['calls'], 2)
            self.assertEqual(self.run_book(retry_failed=True)['calls'], 0)
        records = [json.loads(line) for line in errors.getvalue().splitlines()]
        self.assertTrue(records)
        self.assertTrue(all(row['error_type'] == 'RuntimeError' for row in records))
        self.assertNotIn('private failure text', errors.getvalue())
        budget = read(self.root / 'runtime/calls.json')
        self.assertTrue(all(s['attempts'] == 2 for s in budget.values()))

    def test_validators_and_fallback_priorities(self):
        self.run_book()
        a = self.artifact()
        state = a['states']['demo']['state']
        facts = a['facts']['demo']
        state['sources'] = ['UNKNOWN']
        with self.assertRaisesRegex(ValueError, 'Unknown project source'):
            daybook.validate_state(state, facts, [])
        state['sources'] = []
        state['start_here']['sources'] = []
        state['start_here']['where'] = {'branch': 'not-in-facts'}
        result, corrections = daybook.validate_state(state, facts, [])
        self.assertEqual((result['start_here']['where']['branch'], corrections), ('', 1))
        state['start_here']['step'] = 'first\nsecond'
        with self.assertRaises(ValueError):
            daybook.validate_state(state, facts, [])
        standing = {'has_remote': True, 'open_pull_requests': [{'number': 1, 'head_label': 'feature', 'draft': False, 'checks': {'failure': 1}}],
                    'worktrees': [{'label': 'tree', 'branch': 'feature', 'modified': 1}],
                    'unpushed_branches': [{'name': 'feature', 'ahead': 1}]}
        f = {'standing': standing}
        self.assertTrue(daybook.fallback_start(f)['step'].startswith('Fix'))
        standing['open_pull_requests'][0]['checks'] = {}
        self.assertTrue(daybook.fallback_start(f)['step'].startswith('Review'))
        standing['open_pull_requests'][0]['draft'] = True
        self.assertTrue(daybook.fallback_start(f)['step'].startswith('Finish'))
        standing['worktrees'] = []
        self.assertTrue(daybook.fallback_start(f)['step'].startswith('Push'))
        standing['unpushed_branches'] = []
        self.assertTrue(daybook.fallback_start(f)['step'].startswith('Pick up'))
        standing['open_pull_requests'] = []
        self.assertTrue(daybook.fallback_start(f)['step'].startswith('Re-read'))

    def test_call_budget_defers_and_empty_raises(self):
        self.config['projects'].append({'id': 'other', 'aliases': ['other']})
        save(self.root / 'claude/other.jsonl', [claude(cwd='/synthetic/user/projects/other')])
        self.config['max_project_calls'] = 1
        result = self.run_book()
        self.assertEqual(result['calls'], 2)
        self.assertEqual(len(self.artifact()['coverage']['deferred_projects']), 1)
        self.config['runtime_dir'] = str(self.root / 'empty-runtime')
        self.config['machines'] = [{'name': 'machine-a'}]
        self.assertEqual(self.run_book(no_model=True)['calls'], 0)


    def test_unconfigured_project_survives_incremental_cache_replay(self):
        self.config['projects'] = []
        self.assertEqual(self.run_book(no_model=True)['status'], 'written')
        self.assertEqual(self.run_book(no_model=True)['status'], 'unchanged')

    def test_deferred_projects_do_not_create_calls_on_identical_rerun(self):
        self.config['projects'].append({'id': 'other', 'aliases': ['other']})
        save(self.root / 'claude/other.jsonl', [claude(cwd='/synthetic/user/projects/other')])
        self.config['max_project_calls'] = 1
        self.assertEqual(self.run_book()['calls'], 2)
        self.assertEqual(self.artifact()['coverage']['deferred_projects'], ['other'])
        result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('unchanged', 0))

    def test_metadata_only_refresh_and_unrelated_evidence_do_not_admit_deferred(self):
        self.config['projects'].append({'id': 'other', 'aliases': ['other']})
        save(self.root / 'claude/other.jsonl', [claude(cwd='/synthetic/user/projects/other')])
        self.config['max_project_calls'] = 1
        self.run_book()
        stamp = self.path.stat().st_mtime + 10
        os.utime(self.path, (stamp, stamp))
        result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('unchanged', 0))
        self.assertEqual(self.artifact()['coverage']['deferred_projects'], ['other'])
        save(self.path, [claude(), claude('A synthetic handoff', role='assistant'), claude('Later owner evidence')])
        self.assertEqual(self.run_book()['calls'], 0)
        self.assertEqual(self.artifact()['coverage']['deferred_projects'], ['other'])

    def test_mtime_and_live_assistant_churn_leave_snapshot_unchanged(self):
        with self.path.open('a') as handle:
            handle.write(json.dumps(claude('Today owner request', at='2026-10-08T09:00:00Z')) + '\n')
            handle.write(json.dumps(claude('Still working', role='assistant', at='2026-10-08T09:05:00Z')) + '\n')
        self.run_book()
        before = self.artifact()
        paths = [self.root / 'runtime/briefs/2026-10-08.json', self.root / 'runtime/briefs/2026-10-08.md']
        mtimes = [p.stat().st_mtime_ns for p in paths]
        stamp = self.path.stat().st_mtime + 60
        os.utime(self.path, (stamp, stamp))
        with patch('second_brain.daybook._model_states', side_effect=AssertionError('reuse shortcut missed')):
            self.assertEqual(self.run_book()['status'], 'unchanged')
        with self.path.open('a') as handle:
            handle.write(json.dumps(claude('More agent progress', role='assistant', at='2026-10-08T10:00:00Z')) + '\n')
        save(self.root / 'claude/subagents/new.jsonl', [claude('Delegated live work', at='2026-10-08T10:01:00Z')])
        self.now += timedelta(minutes=1)
        with patch('second_brain.daybook._model_states', side_effect=AssertionError('reuse shortcut missed')):
            result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('unchanged', 0))
        self.assertEqual(self.artifact(), before)
        self.assertEqual([p.stat().st_mtime_ns for p in paths], mtimes)
        with self.path.open('a') as handle:
            handle.write(json.dumps(claude('A new owner decision', at='2026-10-08T10:05:00Z')) + '\n')
        result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('refreshed', 0))
        self.assertEqual(self.artifact()['states'], before['states'])

    def test_scrubbed_coverage_reuses_model_and_source_failure_refreshes(self):
        original = self.boundary.clean
        self.boundary.clean = lambda text: original(text).replace('synthetic coverage note', 'safe note')
        with patch('second_brain.daybook.localgit.collect', return_value=([], {}, {'note': 'synthetic coverage note'})):
            self.run_book()
            self.assertEqual(self.artifact()['coverage']['localgit']['note'], 'safe note')
            with patch('second_brain.daybook._model_states', side_effect=AssertionError('reuse shortcut missed')):
                self.assertEqual(self.run_book()['status'], 'unchanged')
        with patch('second_brain.daybook.localgit.collect', return_value=([], {}, {'failures': [{'stage': 'collect.localgit', 'error_type': 'Unavailable'}]})), \
             patch('second_brain.daybook._model_states', side_effect=AssertionError('coverage triggered model')):
            result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('partial', 0))

    def test_model_run_after_deterministic_delivery_still_admits(self):
        self.run_book(no_model=True)
        self.assertEqual(self.run_book()['calls'], 2)

    def test_display_preference_refreshes_markdown(self):
        self.run_book(no_model=True)
        self.config['display_names'] = 'real'
        self.assertEqual(self.run_book(no_model=True)['status'], 'refreshed')
        self.assertIn('### demo (demo)', self.artifact()['markdown'])

    def test_interrupted_attempt_is_durable_and_retryable(self):
        self.model.summarize = lambda prompt: (_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.run_book()
        budget = read(self.root / 'runtime/calls.json')
        self.assertEqual(next(iter(budget.values()))['status'], 'attempted')
        self.model = Model()
        self.assertEqual(self.run_book(retry_failed=True)['calls'], 2)
        budget = read(self.root / 'runtime/calls.json')
        self.assertEqual(budget['2026-10-08:daybook:2026-10-07:demo']['attempts'], 2)
