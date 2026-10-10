# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Native-ID corrections and stale mirrors from the third audit, synthetically."""
import os

from second_brain import daybook, sessions
from second_brain.storage import read
from tests.daybook_fixtures import claude, codex, save
from tests.test_daybook_run import RunFixture


class DedupTests(RunFixture):
    def test_native_correction_stale_mirror_and_convergence_in_every_scan_order(self):
        for tool, make in [('claude', claude), ('codex', codex)]:
            for mirror_first in (False, True):
                for edit_mirror in (False, True):
                    with self.subTest(tool=tool, mirror_first=mirror_first, edit_mirror=edit_mirror):
                        root = self.root / f'{tool}-{mirror_first}-{edit_mirror}'
                        original, mirror = root / 'original/a.jsonl', root / 'mirror/a.jsonl'
                        mirror.parent.mkdir(parents=True)
                        original.parent.mkdir()
                        config = {**self.config, 'runtime_dir': str(root / 'runtime'), 'machines': [
                            {'name': 'machine-a', tool: [str(original.parent)]},
                            {'name': 'machine-0' if mirror_first else 'machine-z', tool: [str(mirror.parent)]}]}
                        meta = [{'type': 'session_meta', 'payload': {'id': 'synthetic-session',
                                 'cwd': '/synthetic/user/projects/demo'}}] if tool == 'codex' else []
                        id_field = 'uuid' if tool == 'claude' else 'id'
                        old = make('Synthetic request alpha', **{id_field: 'synthetic-turn'})
                        new = make('Synthetic request bravo', **{id_field: 'synthetic-turn'})
                        def run():
                            return daybook.run(config, self.boundary, self.model, now=self.now)
                        def artifact():
                            return read(root / 'runtime/briefs/2026-10-08.json')
                        def snapshot():
                            return [(p.read_bytes(), p.stat().st_mtime_ns)
                                    for p in sorted((root / 'runtime/briefs').iterdir())]
                        save(original, meta + [old])
                        self.assertEqual(run()['calls'], 2)
                        initial = snapshot()
                        saved_states = artifact()['states']
                        budget = (root / 'runtime/calls.json').read_bytes()
                        save(mirror, meta + [old])
                        self.assertEqual((run()['status'], snapshot()), ('unchanged', initial))
                        edited = mirror if edit_mirror else original
                        stale = original if edit_mirror else mirror
                        size = edited.stat().st_size
                        mtime = edited.stat().st_mtime_ns
                        save(edited, meta + [new])
                        os.utime(edited, ns=(mtime, mtime))
                        self.assertEqual(edited.stat().st_size, size)
                        changed = run()
                        self.assertEqual((changed['status'], changed['calls']), ('refreshed', 0))
                        self.assertEqual(artifact()['facts']['demo']['yesterday']['owner_messages_total'], 1)
                        # Inspect collection, not saved model prose, which intentionally lags.
                        _, evidence, coverage = sessions.collect(config, self.registry, self.win, self.boundary,
                            root / 'runtime/cache/sessions')
                        self.assertEqual([e['text'] for e in evidence if e['kind'] == 'owner_message'], ['Synthetic request bravo'])
                        self.assertEqual(sum(c['duplicate_rows_yesterday'] for t in coverage.values() for c in t.values()), 1)
                        corrected = snapshot()
                        # A newly arriving stale copy and fresh mtime must not win.
                        save(stale.parent / '0-late-stale.jsonl', meta + [old])
                        os.utime(stale, None)
                        self.assertEqual((run()['status'], snapshot()), ('unchanged', corrected))
                        # Converge either copy first, preserving canonical source credit.
                        for path in (stale, stale.parent / '0-late-stale.jsonl'):
                            save(path, meta + [new])
                            result = run()
                            self.assertEqual((result['status'], result['calls']), ('unchanged', 0))
                            self.assertEqual(snapshot(), corrected)
                            self.assertEqual(artifact()['states'], saved_states)
                            self.assertEqual((root / 'runtime/calls.json').read_bytes(), budget)
                        self.assertEqual(artifact()['facts']['demo']['yesterday']['owner_messages_total'], 1)

    def test_native_corrections_without_mirror_then_late_stale_sync(self):
        old = claude('Synthetic request alpha', uuid='synthetic-turn')
        new = claude('Synthetic request bravo', uuid='synthetic-turn')
        save(self.path, [old])
        self.run_book()
        save(self.path, [new])
        self.assertEqual(self.run_book()['status'], 'refreshed')
        before = self.artifact()
        save(self.root / 'claude/0-stale.jsonl', [old])
        self.assertEqual(self.run_book()['status'], 'unchanged')
        self.assertEqual(self.artifact(), before)

    def test_unordered_cold_versions_are_one_visible_unattributed_conflict(self):
        for index, values in enumerate([('alpha', 'bravo'), ('bravo', 'alpha')]):
            with self.subTest(order=values):
                folder = self.root / f'cold-{index}'
                config = {**self.config, 'runtime_dir': str(folder / 'runtime'),
                          'machines': [{'name': 'machine-a', 'claude': [str(folder / 'source')]}]}
                save(folder / 'source/a.jsonl', [claude('Synthetic ' + values[0], uuid='same-turn')])
                save(folder / 'source/b.jsonl', [claude('Synthetic ' + values[1], uuid='same-turn')])
                result = daybook.run(config, self.boundary, self.model, now=self.now)
                self.assertEqual(result['calls'], 0)
                artifact = read(folder / 'runtime/briefs/2026-10-08.json')
                cov = artifact['coverage']['sessions']['machine-a']['claude']
                self.assertEqual((cov['owner_messages_yesterday'], cov['unattributed_user_yesterday'],
                                  cov['version_conflicts_yesterday'], cov['duplicate_rows_yesterday']), (0, 1, 1, 1))
                self.assertEqual(artifact['active'], [])
                self.assertIn('Unordered native-message conflicts yesterday: 1', artifact['markdown'])

    def test_native_identity_prefers_explicit_copy_then_uses_content_fallback(self):
        save(self.path, [claude('Same request', uuid='one', author=None),
                         claude('Same request', uuid='two'), claude('Same request', uuid='two')])
        save(self.root / 'claude/z-explicit.jsonl', [claude('Same request', uuid='one')])
        # Distinct native IDs survive equality; an ID-less mirror is only joined
        # when its content corresponds to one native turn, not multiple turns.
        save(self.root / 'claude/fallback.jsonl', [claude('Other request', uuid='three'),
                                                claude('Other request'), claude('Other request')])
        _, evidence, coverage = sessions.collect(self.config, self.registry, self.win, self.boundary, self.root / 'cache')
        cov = coverage['machine-a']['claude']
        self.assertEqual((cov['owner_messages_yesterday'], cov['owner_explicit_yesterday'],
                          cov['unattributed_user_yesterday'], cov['duplicate_rows_yesterday']), (3, 3, 0, 4))
        self.assertEqual(len([e for e in evidence if e['kind'] == 'owner_message']), 3)

    def test_case_only_native_correction_is_a_real_change(self):
        save(self.path, [claude('Synthetic request alpha', uuid='case-turn')])
        self.run_book()
        save(self.root / 'claude/mirror.jsonl', [claude('Synthetic request alpha', uuid='case-turn')])
        self.assertEqual(self.run_book()['status'], 'unchanged')
        save(self.path, [claude('SYNTHETIC request alpha', uuid='case-turn')])
        self.assertEqual(self.run_book()['status'], 'refreshed')
        self.assertEqual(self.artifact()['facts']['demo']['yesterday']['owner_messages_total'], 1)
        self.assertEqual(self.run_book()['status'], 'unchanged')

    def test_same_file_repeated_native_turn_uses_last_occurrence(self):
        for index, reverse in enumerate((False, True)):
            with self.subTest(reverse=reverse):
                folder = self.root / f'replay-{index}'
                config = {**self.config, 'runtime_dir': str(folder / 'runtime'),
                          'machines': [{'name': 'machine-a', 'claude': [str(folder / 'source')]}]}
                path = folder / 'source/a.jsonl'
                old = claude('Synthetic request alpha', uuid='same-turn')
                new = claude('Synthetic request bravo', uuid='same-turn')
                save(path, [old])
                daybook.run(config, self.boundary, self.model, now=self.now)
                save(path, [new, old] if reverse else [old, new])
                result = daybook.run(config, self.boundary, self.model, now=self.now)
                self.assertEqual((result['status'], result['calls']), ('refreshed', 0))
                _, evidence, coverage = sessions.collect(config, self.registry, self.win, self.boundary,
                    folder / 'runtime/cache/sessions')
                self.assertEqual([e['text'] for e in evidence if e['kind'] == 'owner_message'],
                                 ['Synthetic request alpha' if reverse else 'Synthetic request bravo'])
                self.assertEqual(coverage['machine-a']['claude']['owner_messages_yesterday'], 1)
                save(path, [new])
                result = daybook.run(config, self.boundary, self.model, now=self.now)
                self.assertEqual((result['status'], result['calls']), ('refreshed', 0))
                self.assertEqual(daybook.run(config, self.boundary, self.model, now=self.now)['status'], 'unchanged')

    def test_later_native_id_mirror_preserves_idless_source_credit(self):
        save(self.path, [claude('Same request')])
        self.run_book()
        initial = self.artifact()
        save(self.root / 'claude/0-native.jsonl', [claude('Same request', uuid='native-turn')])
        self.assertEqual((self.run_book()['status'], self.artifact()), ('unchanged', initial))
