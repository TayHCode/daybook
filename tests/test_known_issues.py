# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Synthetic brief presentation and disagreeing handoff regressions."""
from second_brain import sessions
from second_brain.storage import fingerprint
from tests.daybook_fixtures import claude, codex, save
from tests.test_daybook_run import RunFixture


class KnownIssuesTests(RunFixture):
    def test_zero_conflicts_have_no_diagnostic_line(self):
        self.run_book(no_model=True)
        self.assertNotIn('Unordered native-message conflicts yesterday:', self.artifact()['markdown'])

    def test_two_sources_show_both_handoffs_with_and_without_model(self):
        owner = claude('Review the parser', uuid='owner')
        save(self.path, [owner, claude('Earlier handoff: add parser tests.', role='assistant', uuid='handoff')])
        save(self.root / 'claude/z-copy.jsonl', [owner, claude('Chosen handoff: review error handling.',
             role='assistant', uuid='handoff', at='2026-10-07T18:00:00Z')])
        for no_model in (True, False):
            with self.subTest(no_model=no_model):
                self.run_book(no_model=no_model)
                before = self.artifact()['markdown']
                self.assertIn('Latest assistant handoff (conflict, unverified): Chosen handoff: review error handling.', before)
                self.assertIn('Unordered native-message conflicts yesterday: 1.', before)
                self.assertIn('a competing handoff from session:', before)
                self.assertIn('also exists: Earlier handoff: add parser tests.', before)
                self.assertEqual(self.artifact()['facts']['demo']['yesterday']['owner_messages_total'], 1)
                self.assertEqual(self.run_book(no_model=no_model)['status'], 'unchanged')
                self.assertEqual(self.artifact()['markdown'], before)

    def test_competing_handoffs_keep_source_and_clean_text_for_both_tools(self):
        for tool, make, id_field in (('claude', claude, 'uuid'), ('codex', codex, 'id')):
            with self.subTest(tool=tool):
                folder = self.root / ('copies-' + tool)
                for name, text, at in (('a.jsonl', 'SYNTHETIC_PRIVATE alternative', '2026-10-07T13:00:00Z'),
                                       ('z.jsonl', 'Chosen handoff', '2026-10-07T18:00:00Z')):
                    rows = [make(text, role='assistant', at=at, **{id_field: 'handoff'})]
                    if tool == 'codex':
                        rows.insert(0, {'type': 'session_meta', 'payload': {'cwd': '/synthetic/user/projects/demo'}})
                    save(folder / name, rows)
                config = {**self.config, 'machines': [{'name': 'machine-a', tool: [str(folder)]}]}
                _, evidence, coverage = sessions.collect(config, self.registry, self.win, self.boundary,
                                                         self.root / ('cache-' + tool))
                handoffs = [e for e in evidence if e['kind'] == 'closing_handoff']
                self.assertEqual(len(handoffs), 1)
                competing = handoffs[0]['competing_handoffs']
                self.assertEqual([c['text'] for c in competing], ['generic alternative'])
                self.assertTrue(competing[0]['source'].startswith('session:'))
                expected_source = 'session:' + fingerprint(['machine-a', tool, str((folder / 'a.jsonl').resolve())])[:16]
                self.assertTrue(competing[0]['source'].startswith(expected_source + ':'))
                self.assertEqual(coverage['machine-a'][tool]['version_conflicts_yesterday'], 1)

    def test_resolved_stream_does_not_show_competing_handoff(self):
        owner = claude('Review parser', uuid='owner')
        early = claude('Earlier stream chunk', role='assistant', uuid='handoff')
        final = claude('Final streamed handoff', role='assistant', uuid='handoff')
        save(self.path, [owner, early, final])
        save(self.root / 'claude/z-mirror.jsonl', [owner, early])
        self.run_book(no_model=True)
        markdown = self.artifact()['markdown']
        self.assertIn('Final streamed handoff', markdown)
        self.assertNotIn('Earlier stream chunk', markdown)
        self.assertNotIn('a competing handoff', markdown)
