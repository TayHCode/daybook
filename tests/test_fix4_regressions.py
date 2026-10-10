# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Synthetic stream ordering, ambiguous copies and JSON diagnostics."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import random
from unittest.mock import patch

from second_brain import cli, daybook, sessions
from second_brain.storage import read, write
from tests.daybook_fixtures import claude, codex, save
from tests.test_daybook_run import RunFixture


class StreamTests(RunFixture):
    def test_stream_last_occurrence_is_one_final_handoff_for_both_tools(self):
        for tool, make, id_field in (('claude', claude, 'uuid'), ('codex', codex, 'id')):
            with self.subTest(tool=tool):
                source = self.root / ('inputs-' + tool) / 'stream.jsonl'
                rows = [make('Refactor parser', **{id_field: 'owner'}),
                        make('Intermediate chunk', role='assistant', **{id_field: 'handoff'}),
                        make('Latest handoff: document error handling.', role='assistant', **{id_field: 'handoff'})]
                if tool == 'codex':
                    rows.insert(0, {'type': 'session_meta', 'payload': {'cwd': '/synthetic/user/projects/demo'}})
                save(source, rows)
                config = {**self.config, 'runtime_dir': str(self.root / ('state-' + tool)),
                          'machines': [{'name': 'machine-a', tool: [str(source.parent)]}]}
                _, evidence, coverage = sessions.collect(config, self.registry, self.win, self.boundary,
                                                         self.root / ('cache-' + tool))
                handoffs = [e for e in evidence if e['kind'] == 'closing_handoff']
                self.assertEqual([e['text'] for e in handoffs], ['Latest handoff: document error handling.'])
                self.assertEqual(coverage['machine-a'][tool]['version_conflicts_yesterday'], 0)

    def check_fresh_states(self, count=8, mirror=False):
        original_rglob = Path.rglob
        for iteration in range(count):
            source = self.root / f'inputs-{mirror}-{iteration}'
            owner = claude('Refactor parser', uuid='owner')
            early = claude('Intermediate chunk', role='assistant', uuid='handoff')
            final = claude('Latest handoff: document error handling.', role='assistant', uuid='handoff')
            files = [('z-stream.jsonl', [owner, early, final])]
            if mirror:
                files.append(('a-mirror.jsonl', [owner, early]))
            rng = random.Random(iteration)
            rng.shuffle(files)
            for name, rows in files:
                save(source / name, rows)
                if iteration % 2 == 0:
                    os.utime(source / name, (1791378000, 1791378000))
            def shuffled(path, pattern, *args, **kwargs):
                values = list(original_rglob(path, pattern, *args, **kwargs))
                rng.shuffle(values)
                return iter(values)
            config = {**self.config, 'runtime_dir': str(self.root / f'state-{mirror}-{iteration}'),
                      'machines': [{'name': 'machine-a', 'claude': [str(source)]}]}
            with patch.object(Path, 'rglob', shuffled):
                first = daybook.run(config, self.boundary, None, now=self.now, no_model=True)
                before = Path(first['path']).read_bytes()
                json_before = Path(first['path']).with_suffix('.json').read_bytes()
                second = daybook.run(config, self.boundary, None, now=self.now, no_model=True)
            self.assertEqual(before, Path(second['path']).read_bytes())
            self.assertEqual(json_before, Path(second['path']).with_suffix('.json').read_bytes())
            self.assertIn(b'Latest handoff: document error handling.', before)
            self.assertNotIn(b'Intermediate chunk', before)
            artifact = read(Path(first['path']).with_suffix('.json'))
            self.assertEqual(artifact['coverage']['sessions']['machine-a']['claude']['version_conflicts_yesterday'], 0)
            self.assertNotIn(b'Unordered native-message conflicts yesterday:', before)
            self.assertEqual(artifact['facts']['demo']['yesterday']['owner_messages_total'], 1)

    def test_fresh_streamed_briefs_with_shuffled_sources_and_equal_mtimes(self):
        self.check_fresh_states()

    def test_fresh_stream_with_stale_mirror_has_no_conflict(self):
        self.check_fresh_states(mirror=True)

    def test_append_and_equal_size_equal_mtime_rewrite_keep_final_text(self):
        owner = claude('Refactor parser', uuid='owner')
        early = claude('Partial handoff: review parser.', role='assistant', uuid='handoff')
        final = claude('Latest handoff: review parser.', role='assistant', uuid='handoff')
        save(self.path, [owner, early])
        self.run_book(no_model=True)
        with self.path.open('a') as handle:
            handle.write(json.dumps(final) + '\n')
        self.run_book(no_model=True)
        self.assertIn('Latest handoff: review parser.', self.artifact()['markdown'])
        before = self.path.stat()
        self.path.write_bytes(self.path.read_bytes().replace(b'review parser', b'review errors'))
        self.assertEqual(self.path.stat().st_size, before.st_size)
        os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.run_book(no_model=True)
        markdown = self.artifact()['markdown']
        self.assertIn('Latest handoff: review errors.', markdown)
        self.assertNotIn('Latest handoff: review parser.', markdown)
        self.assertEqual(self.run_book(no_model=True)['status'], 'unchanged')

    def test_last_stream_occurrence_can_repeat_an_earlier_content_value(self):
        save(self.path, [claude('Refactor parser', uuid='owner'),
                         claude('Final handoff', role='assistant', uuid='handoff'),
                         claude('Intermediate handoff', role='assistant', uuid='handoff'),
                         claude('Final handoff', role='assistant', uuid='handoff')])
        self.run_book(no_model=True)
        self.assertIn('Final handoff', self.artifact()['markdown'])
        self.assertNotIn('Intermediate handoff', self.artifact()['markdown'])
        self.assertEqual(self.artifact()['coverage']['sessions']['machine-a']['claude']['version_conflicts_yesterday'], 0)

    def test_observed_stream_reversion_supersedes_a_stale_mirror(self):
        owner = claude('Refactor parser', uuid='owner')
        alpha = claude('Handoff alpha', role='assistant', uuid='handoff')
        bravo = claude('Handoff bravo', role='assistant', uuid='handoff')
        save(self.path, [owner, alpha, bravo])
        self.run_book(no_model=True)
        save(self.root / 'claude/mirror.jsonl', [owner, bravo])
        with self.path.open('a') as handle:
            handle.write(json.dumps(alpha) + '\n')
        self.run_book(no_model=True)
        self.assertIn('Handoff alpha', self.artifact()['markdown'])
        self.assertNotIn('Handoff bravo', self.artifact()['markdown'])
        self.assertEqual(self.artifact()['coverage']['sessions']['machine-a']['claude']['version_conflicts_yesterday'], 0)

    def test_final_stream_position_wins_among_equal_timestamp_handoffs(self):
        save(self.path, [claude('Refactor parser', uuid='owner'),
                         claude('Partial stream', role='assistant', uuid='stream'),
                         claude('Intervening update', role='assistant', uuid='other'),
                         claude('Latest streamed handoff', role='assistant', uuid='stream')])
        self.run_book(no_model=True)
        self.assertIn('Latest assistant handoff (unverified): Latest streamed handoff', self.artifact()['markdown'])
        self.assertNotIn('Latest assistant handoff (unverified): Intervening update', self.artifact()['markdown'])

    def test_true_two_source_conflict_retains_latest_handoff_and_stays_ambiguous(self):
        owner = claude('Refactor parser', uuid='owner')
        save(self.path, [owner, claude('Older ambiguous handoff', role='assistant', uuid='handoff')])
        save(self.root / 'claude/z-copy.jsonl', [owner, claude('Latest ambiguous handoff', role='assistant',
                                                          uuid='handoff', at='2026-10-07T18:00:00Z')])
        self.run_book(no_model=True)
        before = self.artifact()['markdown']
        self.assertIn('Latest assistant handoff (conflict, unverified): Latest ambiguous handoff', before)
        self.assertEqual(self.artifact()['coverage']['sessions']['machine-a']['claude']['version_conflicts_yesterday'], 1)
        self.assertEqual(self.run_book(no_model=True)['status'], 'unchanged')
        self.assertEqual(self.artifact()['markdown'], before)
        self.run_book()
        self.assertIn('Latest assistant handoff (conflict, unverified): Latest ambiguous handoff',
                      self.artifact()['markdown'])
        self.assertIn('"verification": "conflict"', self.model.calls[0])

    def test_stream_message_id_reuse_in_another_checkout_does_not_merge_projects(self):
        save(self.path, [claude('First checkout request', uuid='owner'),
                         claude('First checkout handoff', role='assistant', uuid='handoff'),
                         claude('Other checkout request', cwd='/synthetic/user/projects/other', uuid='owner'),
                         claude('Other partial handoff', role='assistant', cwd='/synthetic/user/projects/other', uuid='handoff'),
                         claude('Other final handoff', role='assistant', cwd='/synthetic/user/projects/other', uuid='handoff')])
        self.run_book(no_model=True)
        artifact = self.artifact()
        for label in ('demo', 'other'):
            self.assertEqual(artifact['facts'][label]['yesterday']['owner_messages_total'], 1)
        self.assertIn('First checkout handoff', json.dumps(artifact['states']['demo']))
        self.assertNotIn('Other final handoff', json.dumps(artifact['states']['demo']))
        self.assertIn('Other final handoff', json.dumps(artifact['states']['other']))


class JsonTests(RunFixture):
    def test_missing_model_cli_json_has_content_free_reason_on_first_and_cached_runs(self):
        config = {**self.config, 'model': 'claude', 'executable_path': [], 'executables': {}}
        path = self.root / 'config.json'
        write(path, config)
        actual_run = daybook.run
        def dated_run(*args, **kwargs):
            return actual_run(*args, **kwargs, now=self.now)
        for _ in range(2):
            output, errors = io.StringIO(), io.StringIO()
            with patch('second_brain.cli.daybook.run', side_effect=dated_run), \
                 patch('second_brain.cli.Boundary', return_value=self.boundary), redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(cli.main(['brief', '--config', str(path), '--allow-network', '--json']), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result['partial'])
            self.assertEqual(result['reason'], 'model_cli_unavailable')
            self.assertIn({'code': 'model_cli_unavailable', 'stage': 'model.preflight',
                           'error_type': 'UserError'}, result['reasons'])
            self.assertTrue(all(set(r) <= {'code', 'stage', 'error_type', 'count'} for r in result['reasons']))
            self.assertNotIn('Build the synthetic feature', output.getvalue())
