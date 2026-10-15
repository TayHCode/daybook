# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Third-audit relay reproductions, using invented native envelopes only."""
import json
from unittest.mock import patch

from second_brain import daybook, sessions
from tests.daybook_fixtures import claude, codex, save
from tests.test_daybook_run import RunFixture


class AttributionTests(RunFixture):
    def test_native_unmarked_relays_and_verified_echoes_in_both_formats(self):

        for tool, make in [('claude', claude), ('codex', codex)]:
            for variant in ('Synthetic request alpha', ' SYNTHETIC\n request ALPHA ',
                            'Synthetic request alpha!'):
                with self.subTest(tool=tool, variant=variant):
                    folder = self.root / tool
                    config = {**self.config, 'machines': [{'name': 'machine-a', tool: [str(folder)]}]}
                    meta = [{'type': 'session_meta', 'payload': {'id': 'synthetic-session',
                            'cwd': '/synthetic/user/projects/demo', 'originator': 't3-code', 'source': 'cli'}}] if tool == 'codex' else []
                    save(folder / 'a.jsonl', meta + [
                        make(variant, author=None, at='2026-10-07T14:00:00Z'),
                        make('Synthetic request alpha', role='assistant', at='2026-10-07T12:00:00Z'),
                        make('Typed request', author='human', at='2026-10-07T15:00:00Z'),
                        make('Typed request', role='assistant', at='2026-10-07T16:00:00Z')])
                    records, evidence, coverage = sessions.collect(config, self.registry, self.win,
                        self.boundary, self.root / ('cache-' + tool))
                    cov = coverage['machine-a'][tool]
                    self.assertEqual((cov['owner_messages_yesterday'], cov['owner_explicit_yesterday'],
                                      cov['unattributed_user_yesterday'], cov['owner_inferred_yesterday']), (1, 1, 1, 0))
                    self.assertEqual([e['text'] for e in evidence if e['kind'] == 'owner_message'], ['Typed request'])
                    self.assertEqual(sum(d['unattributed_messages'] for r in records for d in r['days'].values()), 1)

    def test_unmarked_relay_only_format_briefs_coverage_without_activity_or_calls(self):
        save(self.path, [claude('Ambiguous synthetic relay!', author=None),
                         claude('Ambiguous synthetic relay.', role='assistant')])
        result = self.run_book()
        self.assertEqual((result['status'], result['calls']), ('written', 0))
        artifact = self.artifact()
        self.assertEqual((artifact['active'], artifact['worked_yesterday'], artifact['states']), ([], [], {}))
        facts = artifact['facts']['demo']
        self.assertEqual(facts['yesterday']['owner_messages_total'], 0)
        self.assertEqual(facts['yesterday']['sessions_total'], 0)
        self.assertEqual(facts['standing']['days'], {})
        self.assertIn('0 explicit owner; 1 unattributed user turns; 0 excluded user rows', artifact['markdown'])
        self.assertNotIn('Ambiguous synthetic relay', artifact['markdown'])
        self.assertEqual((self.run_book()['status'], len(self.model.calls)), ('unchanged', 0))

    def test_independent_owner_activity_never_promotes_ambiguous_words(self):
        # A same-day owner signal can activate its project independently. A signal
        # on another project/day cannot turn the relay into activity for yesterday.
        for kind in ('owner_message', 'commit', 'local_commit', 'pull_request', 'branch_activity'):
            with self.subTest(kind=kind):
                save(self.path, [claude('UNATTRIBUTED_REQUEST_DO_NOT_PROMPT', author=None),
                                 *([claude('Verified typed request', at='2026-10-07T15:00:00Z')]
                                   if kind == 'owner_message' else [])])
                event = {'id': 'verified-' + kind, 'source': 'synthetic:verified', 'project': 'demo',
                         'event_at': '2026-10-07T15:00:00Z', 'period': 'yesterday', 'kind': kind,
                         'verification': 'catalog', 'text': 'Verified owner activity', 'actor': 'Owner',
                         'sha': 'abc123', 'subject': 'Verified change', 'branches': ['feature'],
                         'number': 7, 'title': 'Verified change', 'event': 'opened', 'head_label': 'feature',
                         'label': 'feature', 'created': 1}
                config = {**self.config, 'runtime_dir': str(self.root / ('runtime-' + kind))}
                with patch('second_brain.daybook.sources.collect', return_value=([] if kind == 'owner_message' else [event], {}, {})):
                    result = daybook.run(config, self.boundary, self.model, now=self.now)
                from second_brain.storage import read
                artifact = read(self.root / ('runtime-' + kind) / 'briefs/2026-10-08.json')
                self.assertEqual(result['calls'], 2)
                self.assertEqual(artifact['active'], ['demo'])
                self.assertEqual(artifact['facts']['demo']['yesterday']['owner_messages_total'], int(kind == 'owner_message'))
                self.assertTrue(all('UNATTRIBUTED_REQUEST_DO_NOT_PROMPT' not in p for p in self.model.calls))

    def test_other_project_or_day_owner_signal_does_not_credit_relay(self):
        save(self.path, [claude('Ambiguous relay', author=None),
                         claude('Earlier verified request', at='2026-10-06T15:00:00Z')])
        save(self.root / 'claude/other.jsonl', [claude('Other owner request', cwd='/synthetic/user/projects/other')])
        self.run_book()
        artifact = self.artifact()
        self.assertIn('demo', artifact['active'])
        self.assertNotIn('demo', artifact['worked_yesterday'])
        self.assertEqual(artifact['facts']['demo']['yesterday']['owner_messages_total'], 0)
        for prompt in self.model.calls:
            self.assertNotIn('Ambiguous relay', prompt)

    def test_unmarked_user_author_and_unknown_metadata_stay_unattributed(self):
        save(self.path, [claude('Ordinary role', author=None), claude('User author', author={'role': 'user'}),
                         claude('Unknown author', author='unknown'), claude('Unknown identity', author='somebody')])
        self.run_book()
        cov = self.artifact()['coverage']['sessions']['machine-a']['claude']
        self.assertEqual((cov['owner_messages_yesterday'], cov['unattributed_user_yesterday']), (0, 4))
