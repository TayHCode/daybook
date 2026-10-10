# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
from copy import deepcopy
import json
import os
from pathlib import Path
from unittest.mock import patch
from second_brain import authorship, sessions
from second_brain.dayconfig import configure, window
from second_brain.storage import read, write
from tests.daybook_fixtures import Case, claude, codex, save


class SessionTests(Case):
    def test_current_cwd_routes_each_turn_in_cross_repo_sessions(self):
        save(self.root / 'claude/a.jsonl', [claude('Initial work'),
             claude('Later work elsewhere', cwd='/synthetic/user/projects/other')])
        self.config['machines'][0]['codex'] = [str(self.root / 'codex')]
        save(self.root / 'codex/a.jsonl', [
             {'type': 'session_meta', 'payload': {'cwd': '/synthetic/user/projects/demo'}}, codex('Initial Codex work'),
             {'type': 'turn_context', 'payload': {'cwd': '/synthetic/user/projects/other'}}, codex('Later Codex work')])
        self.registry.resolve = lambda cwd, machine: cwd.rsplit('/', 1)[-1]
        records, evidence, _ = self.collect()
        self.assertEqual(len(evidence), 4)
        self.assertEqual({e['project'] for e in evidence}, {'demo', 'other'})
        self.assertEqual({r['cwd_label'] for r in records}, {'/synthetic/user/projects/demo', '/synthetic/user/projects/other'})

    def test_malformed_nonobject_and_partial_rows_recover_without_losing_valid_rows(self):
        path = self.root / 'claude/a.jsonl'
        save(path, [claude('First valid')])
        with path.open('ab') as handle:
            handle.write(b'{broken\n[]\n')
            handle.write(json.dumps(claude('Second valid')).encode() + b'\n')
            handle.write(json.dumps(claude('Completed later')).encode())
        _, evidence, cov = self.collect()
        self.assertEqual(len(evidence), 2)
        self.assertEqual(cov['machine-a']['claude']['skipped_lines'], 2)
        with path.open('ab') as handle:
            handle.write(b'\n')
        _, evidence, cov = self.collect()
        self.assertEqual(len(evidence), 3)
        self.assertEqual(cov['machine-a']['claude']['skipped_lines'], 2)

    def test_forked_and_cross_machine_history_counted_once(self):
        rows = [claude('First request'), claude('Second request', at='2026-10-07T14:00:00Z')]
        save(self.root / 'claude/a.jsonl', rows)
        save(self.root / 'claude/b.jsonl', rows + [claude('Third request', at='2026-10-07T15:00:00Z')])
        self.config['machines'].append({'name': 'machine-b', 'claude': [str(self.root / 'mirror')]})
        save(self.root / 'mirror/a.jsonl', rows)
        records, evidence, cov = self.collect()
        self.assertEqual(sum(c['owner_messages_yesterday'] for tools in cov.values() for c in tools.values()), 3)
        self.assertEqual(len(evidence), 3)
        self.assertEqual(sum(c['duplicate_rows_yesterday'] for tools in cov.values() for c in tools.values()), 4)
        os.utime(self.root / 'mirror/a.jsonl', None)
        again = self.collect()
        self.assertEqual(again[:2], (records, evidence))
        self.assertEqual(sum(c['owner_messages_yesterday'] for tools in again[2].values() for c in tools.values()), 3)

    def test_repeated_requests_different_times_projects_or_raw_text_survive(self):
        save(self.root / 'claude/a.jsonl', [claude('Repeat'), claude('Repeat', at='2026-10-07T14:00:00Z'),
             claude('generic'), claude('SYNTHETIC_PRIVATE')])
        save(self.root / 'claude/b.jsonl', [claude('Repeat', cwd='/synthetic/user/projects/other')])
        records, evidence, cov = self.collect()
        self.assertEqual(cov['machine-a']['claude']['owner_messages_yesterday'], 5)

    def collect(self):
        return sessions.collect(self.config, self.registry, self.win, self.boundary, self.root / 'cache')

    def test_late_agent_echo_cannot_reclassify_owner_turns(self):
        # Identical words in another transcript cannot establish who typed these.
        candidates = [claude(f'Check synthetic item {i}') for i in range(151)]
        save(self.root / 'claude/a.jsonl', candidates)
        self.assertEqual(self.collect()[2]['machine-a']['claude']['owner_messages_yesterday'], 151)
        self.config['machines'].append({'name': 'machine-b', 'codex': [str(self.root / 'codex')]})
        save(self.root / 'codex/b.jsonl', [codex(f' CHECK\n synthetic ITEM {i} ', role='assistant', channel='commentary') for i in range(41)])
        records, evidence, cov = self.collect()
        self.assertEqual(cov['machine-a']['claude']['owner_messages_yesterday'], 151)
        self.assertEqual(cov['machine-a']['claude']['owner_explicit_yesterday'], 151)
        self.assertEqual(len([e for e in evidence if e['kind'] == 'owner_message']), 151)
        self.assertEqual(sum(d['owner_messages'] for r in records for d in r['days'].values()), 151)
        again = self.collect()
        self.assertEqual(again[:2], (records, evidence))
        self.assertEqual(again[2]['machine-a']['claude']['owner_explicit_yesterday'], 151)

    def test_structural_agent_duplicates_and_prompt_shapes(self):
        save(self.root / 'claude/a.jsonl', [claude('Check the synthetic branch'),
             claude('Check the synthetic branch', isSidechain=True),
             claude('You are reviewing PR 12 in a synthetic repository.'),
             claude('You are implementing the synthetic parser.'),
             claude('Delegated task: inspect synthetic fixtures', author=None),
             claude('A long owner request. ' + 'Explain the synthetic format. ' * 100)])
        _, evidence, cov = self.collect()
        self.assertEqual(cov['machine-a']['claude']['owner_messages_yesterday'], 4)
        self.assertEqual(len(evidence), 4)
        self.assertTrue(any(e['text'].startswith('A long owner request.') for e in evidence))
        for text in ('Please review PR 12', 'You are welcome to explain the design',
                     'I am reviewing this myself', 'This mentions a delegated task: in prose'):
            self.assertEqual(authorship.classify(text, explicit_owner=True)[1], 'owner')

    def test_structural_copies_exact_case_spacing_and_punctuation(self):
        variants = ['Inspect the parser.', ' INSPECT\n the PARSER. ', 'Inspect the parser!']
        save(self.root / 'claude/agent.jsonl', [claude('Inspect the parser.', role='assistant')])
        save(self.root / 'claude/copies.jsonl', [claude(t, authored_by='agent') for t in variants])
        self.config['machines'].append({'name': 'machine-b', 'codex': [str(self.root / 'codex')]})
        save(self.root / 'codex/child.jsonl', [
            {'type': 'session_meta', 'payload': {'cwd': '/synthetic/user/projects/demo',
             'source': {'subagent': {'thread_spawn': {'parent_thread_id': 'synthetic-parent', 'depth': 1}}}}},
            *[codex(t) for t in variants]])
        _, evidence, cov = self.collect()
        self.assertFalse([e for e in evidence if e['kind'] == 'owner_message'])
        self.assertEqual(sum(c['owner_messages_yesterday'] for tools in cov.values() for c in tools.values()), 0)
        # Excluding the original agent file from discovery cannot resurrect copies.
        os.utime(self.root / 'claude/agent.jsonl', (1, 1))
        _, evidence, _ = self.collect()
        self.assertFalse([e for e in evidence if e['kind'] == 'owner_message'])

    def test_agent_user_collision_never_swallows_genuine_owner(self):
        text = 'Inspect the synthetic branch.'
        save(self.root / 'claude/a-agent.jsonl', [claude(text, isSidechain=True)])
        save(self.root / 'claude/z-owner.jsonl', [claude(text, author={'type': 'human'})])
        save(self.root / 'claude/unrelated.jsonl', [claude(text, role='assistant',
             at='2026-10-08T09:00:00Z', cwd='/synthetic/user/projects/other')])
        _, evidence, cov = self.collect()
        self.assertEqual([e['text'] for e in evidence if e['kind'] == 'owner_message'], [text])
        self.assertEqual(cov['machine-a']['claude']['owner_explicit_yesterday'], 1)
        self.assertEqual(cov['machine-a']['claude']['excluded_user_yesterday'], 1)

    def test_explicit_roles_authors_and_tool_blocks_outrank_user_envelope(self):
        rows = [claude('Agent payload', message={'role': 'assistant', 'content': 'Agent payload'}),
                claude('Agent author', message={'role': 'user', 'author': {'role': 'assistant'}, 'content': 'Agent author'}),
                claude('Tool text', author='owner', message={'role': 'user', 'content': [
                    {'type': 'text', 'text': 'Tool text'}, {'type': 'tool_result', 'content': 'Synthetic output'}]}),
                claude('Explicitly unknown', author='unknown'),
                claude('Human request', message={'role': 'user', 'author': 'human', 'content': 'Human request'})]
        save(self.root / 'claude/a.jsonl', rows)
        self.config['machines'][0]['codex'] = [str(self.root / 'codex')]
        save(self.root / 'codex/a.jsonl', [
             {'type': 'session_meta', 'payload': {'cwd': '/synthetic/user/projects/demo'}},
             codex('Agent request', author='agent'),
             codex('Owner request', authored_by='owner'), codex('System context', role='system'),
             {'type': 'event_msg', 'timestamp': '2026-10-07T13:00:00Z',
              'payload': {'type': 'user_message', 'message': 'Owner request'}},
             {'type': 'response_item', 'timestamp': '2026-10-07T13:00:00Z',
              'payload': {'type': 'function_call_output', 'output': 'Tool request'}}])
        _, evidence, cov = self.collect()
        self.assertEqual({e['text'] for e in evidence if e['kind'] == 'owner_message'}, {'Human request', 'Owner request'})
        self.assertEqual(cov['machine-a']['claude']['unresolved_user_yesterday'], 1)
        self.assertEqual(cov['machine-a']['claude']['attribution_reasons_yesterday']['tool_result'], 1)

    def test_resumed_replays_commands_and_reminders_are_not_new_owner_words(self):
        original = claude('Original request', uuid='synthetic-user-id')
        save(self.root / 'claude/a.jsonl', [original])
        save(self.root / 'claude/resumed.jsonl', [original,
             claude('Replayed history', isReplay=True), claude('Summary request', isCompactSummary=True),
             claude('Expanded command', isMeta=True),
             claude('<command-message>review</command-message>\nExpanded instructions', author=None),
             claude('This session is being continued from a previous conversation that ran out of context. Summary', author=None),
             claude('/review the synthetic change', uuid='synthetic-next-id'),
             claude('Fresh request\n<system-reminder>Injected words</system-reminder>')])
        self.config['machines'][0]['codex'] = [str(self.root / 'codex')]
        save(self.root / 'codex/resumed.jsonl', [
             {'type': 'session_meta', 'payload': {'cwd': '/synthetic/user/projects/demo', 'source': 'cli', 'forked_from_id': 'old'}},
             codex('Replayed request', is_replay=True), codex('Fresh resumed request'),
             {'type': 'compacted', 'payload': {'replacement_history': [codex('Summary history')]}}])
        _, evidence, cov = self.collect()
        self.assertEqual({e['text'] for e in evidence if e['kind'] == 'owner_message'},
                         {'Original request', '/review the synthetic change', 'Fresh request', 'Fresh resumed request'})
        self.assertEqual(cov['machine-a']['claude']['duplicate_rows_yesterday'], 1)
        self.assertEqual(self.collect()[1], evidence)

    def test_explicit_owner_and_native_ids_preserve_coincidental_requests(self):
        words = ['You are reviewing PR 12 in a synthetic repository.', 'You are implementing the parser.',
                 'Caveat: this is my request', '<div>Explain this HTML</div>',
                 'Explain <environment_context> as a literal unclosed tag', 'You are the sub-agent expert I need.']
        save(self.root / 'claude/a.jsonl', [claude(t, authored_by='owner') for t in words] + [
             claude('Repeat', uuid='id-one'), claude('Repeat', uuid='id-two'), claude('Repeat', uuid='id-one')])
        _, evidence, cov = self.collect()
        self.assertEqual(len(evidence), 8)
        self.assertEqual(cov['machine-a']['claude']['owner_explicit_yesterday'], 8)
        self.assertEqual(cov['machine-a']['claude']['owner_inferred_yesterday'], 0)

    def test_unmarked_sdk_relays_are_unattributed_and_never_owner_evidence(self):
        # Deliberately reproduce the audit's impossible-to-disambiguate shape.
        # Agent equality cannot prove whether this identical user turn is a relay.
        save(self.root / 'claude/a.jsonl', [claude('Inspect the parser!', author=None, entrypoint='sdk-ts', promptSource='sdk'),
             claude('Inspect the parser!', role='assistant')])
        _, evidence, cov = self.collect()
        self.assertEqual(cov['machine-a']['claude']['unattributed_user_yesterday'], 1)
        self.assertEqual(cov['machine-a']['claude']['owner_messages_yesterday'], 0)
        self.assertEqual([e['attribution'] for e in evidence if e['kind'] == 'owner_message'], [])
        self.assertIn('indistinguishable', authorship.RULE)

    def test_scrubbed_placeholders_do_not_create_authorship_matches(self):
        save(self.root / 'claude/a.jsonl', [claude('generic'), claude('SYNTHETIC_PRIVATE', role='assistant')])
        self.assertEqual(self.collect()[2]['machine-a']['claude']['owner_messages_yesterday'], 1)

    def test_old_cache_reparsed_and_pattern_changes_invalidate_cache(self):
        save(self.root / 'claude/a.jsonl', [claude('custom task')])
        self.collect()
        path = self.root / 'cache/machine-a.claude.json'
        cache = read(path)
        for entry in cache.values():
            entry.pop('authorship_version')
            entry.pop('attributions')
        write(path, cache)
        self.assertEqual(self.collect()[2]['machine-a']['claude']['files_scanned'], 1)
        self.config['injected_patterns'] = ['^custom task']
        self.assertEqual(self.collect()[2]['machine-a']['claude']['owner_messages_yesterday'], 0)

    def test_two_tools_two_machines_and_multiline(self):
        save(self.root / 'claude/a.jsonl', [claude('first\nsecond SYNTHETIC_PRIVATE\nthird')])
        self.config['machines'].append({'name': 'machine-b', 'codex': [str(self.root / 'codex')]})
        save(self.root / 'codex/b.jsonl', [{'type': 'session_meta', 'payload': {'cwd': '/synthetic/user/projects/demo'}}, codex()])
        records, evidence, cov = self.collect()
        self.assertEqual({(r['machine'], r['tool']) for r in records}, {('machine-a', 'claude'), ('machine-b', 'codex')})
        self.assertIn('first\nsecond generic\nthird', [e['text'] for e in evidence])
        self.assertEqual(cov['machine-b']['codex']['owner_messages_yesterday'], 1)

    def test_authorship_structural_and_harness(self):
        rows = [claude('# AGENTS.md instructions for a project', author=None), claude('Act as the implementation sub-agent for this task.', author=None),
                claude('<environment_context>context</environment_context>'), claude('task', isSidechain=True),
                claude('task', sourceToolAssistantUUID='synthetic'), claude('task', isMeta=True),
                claude([{'type': 'tool_result', 'text': 'output'}]), claude('Owner words\n<system-reminder>injected</system-reminder>')]
        save(self.root / 'claude/a.jsonl', rows)
        save(self.root / 'claude/subagents/agent.jsonl', [claude('Delegated work')])
        records, evidence, cov = self.collect()
        self.assertEqual(cov['machine-a']['claude']['owner_messages_yesterday'], 1)
        self.assertEqual([e['text'] for e in evidence], ['Owner words'])
        self.assertEqual(authorship.classify('words <environment_context>unclosed')[1], 'injected')
        self.assertEqual(authorship.classify('custom', extra_injected=['^custom'])[1], 'injected')

    def test_content_probe_fast_path_touch_only_hash_and_growth_resumes(self):
        path = self.root / 'claude/a.jsonl'
        save(path, [claude('first')])
        one = sessions.scan(path, 'claude', False, None, self.config, self.boundary, self.win)
        with patch.object(sessions, '_content_hash', side_effect=AssertionError('unchanged source fully rehashed')):
            two = sessions.scan(path, 'claude', False, one, self.config, self.boundary, self.win)
        self.assertEqual(one, two)
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1000000000))
        with patch.object(sessions, '_row', side_effect=AssertionError('touch-only source reparsed')):
            two = sessions.scan(path, 'claude', False, one, self.config, self.boundary, self.win)
        self.assertEqual(two['messages'], one['messages'])
        self.assertEqual(two['content_sha'], one['content_sha'])
        with path.open('a') as handle:
            handle.write(json.dumps(claude('second')) + '\n')
        with patch.object(sessions, '_row', wraps=sessions._row) as parsed:
            three = sessions.scan(path, 'claude', False, two, self.config, self.boundary, self.win)
        self.assertEqual(parsed.call_count, 1)
        self.assertEqual([m['line'] for m in three['messages']], [1, 2])
        self.assertEqual(three['days']['2026-10-07']['owner_messages'], 2)
        save(path, [claude('replacement considerably longer than before'), claude('another considerably longer replacement')])
        four = sessions.scan(path, 'claude', False, three, self.config, self.boundary, self.win)
        self.assertEqual(four['line'], 2)
        self.assertNotIn('first', [m['text'] for m in four['messages']])

    def test_same_size_interior_correction_with_restored_mtime_and_atomic_replace(self):
        path = self.root / 'claude/a.jsonl'
        rows = [claude(f'Original synthetic request {i:03d}', uuid=f'row-{i}') for i in range(150)]
        save(path, rows)
        first = sessions.scan(path, 'claude', False, None, self.config, self.boundary, self.win)
        stamp = path.stat()
        rows[75]['message']['content'] = 'Correct! synthetic request 075'
        save(path, rows)
        self.assertEqual(path.stat().st_size, stamp.st_size)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        corrected = sessions.scan(path, 'claude', False, first, self.config, self.boundary, self.win)
        self.assertEqual(len(corrected['messages']), 150)
        self.assertEqual(corrected['messages'][75]['text'], 'Correct! synthetic request 075')
        self.assertNotEqual(corrected['content_sha'], first['content_sha'])
        replacement = self.root / 'replacement.jsonl'
        rows[75]['message']['content'] = 'Changed! synthetic request 075'
        save(replacement, rows)
        os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        replacement.replace(path)
        replaced = sessions.scan(path, 'claude', False, corrected, self.config, self.boundary, self.win)
        self.assertEqual(replaced['messages'][75]['text'], 'Changed! synthetic request 075')

    def test_growth_with_old_prefix_correction_and_old_hashless_cache_reparse(self):
        path = self.root / 'claude/a.jsonl'
        rows = [claude(f'Original synthetic request {i:03d}') for i in range(80)]
        save(path, rows)
        first = sessions.scan(path, 'claude', False, None, self.config, self.boundary, self.win)
        rows[0]['message']['content'] = 'Correct! synthetic request 000'
        save(path, rows + [claude('New request')])
        corrected = sessions.scan(path, 'claude', False, first, self.config, self.boundary, self.win)
        self.assertEqual(corrected['messages'][0]['text'], 'Correct! synthetic request 000')
        self.assertEqual(len(corrected['messages']), 81)
        corrected.pop('content_sha')
        with patch.object(sessions, '_row', wraps=sessions._row) as parsed:
            migrated = sessions.scan(path, 'claude', False, corrected, self.config, self.boundary, self.win)
        self.assertEqual(parsed.call_count, 81)
        self.assertEqual(migrated['messages'], corrected['messages'])

    def test_oversized_and_incomplete_line(self):
        path = self.root / 'claude/a.jsonl'
        save(path, [claude('x' * 1000), claude('kept')])
        self.config['max_line_bytes'] = 300
        with path.open('ab') as handle:
            handle.write(json.dumps(claude('partial')).encode())
        one = sessions.scan(path, 'claude', False, None, self.config, self.boundary, self.win)
        self.assertEqual(one['oversized_lines'], 1)
        self.assertEqual(len(one['messages']), 1)
        with path.open('ab') as handle:
            handle.write(b'\n')
        two = sessions.scan(path, 'claude', False, one, self.config, self.boundary, self.win)
        self.assertEqual(len(two['messages']), 2)

    def test_duplicates_missing_roots_and_poison(self):
        save(self.root / 'claude/a.jsonl', [claude('short')])
        save(self.root / 'mirror/a.jsonl', [claude('SYNTHETIC_PRIVATE'), claude('SYNTHETIC_POISON')])
        self.config['machines'][0]['claude'] += [str(self.root / 'mirror'), str(self.root / 'missing')]
        records, evidence, cov = self.collect()
        self.assertEqual(len(records), 2)
        self.assertEqual({e['text'] for e in evidence}, {'short', 'generic'})
        self.assertEqual(cov['machine-a']['claude']['privacy_dropped'], 1)
        self.assertEqual(cov['machine-a']['claude']['status'], 'source_unavailable')
        cached = (self.root / 'cache/machine-a.claude.json').read_text()
        self.assertNotIn('SYNTHETIC_', cached)

    def test_local_day_handoffs_and_pruning(self):
        path = self.root / 'claude/a.jsonl'
        save(path, [claude(at='2026-10-08T03:30:00Z')] + [claude(str(i), role='assistant') for i in range(4)])
        records, evidence, _ = self.collect()
        self.assertEqual(records[0]['days']['2026-10-07']['owner_messages'], 1)
        self.assertEqual(len(evidence), 3)
        cache_path = self.root / 'cache/machine-a.claude.json'
        cache = read(cache_path)
        stale = deepcopy(next(iter(cache.values())))
        stale['mtime_ns'] = 1
        cache['stale'] = stale
        write(cache_path, cache)
        self.collect()
        self.assertNotIn('stale', read(cache_path))

    def test_configuration_validation_and_dst(self):
        for key, value in [('active_days', 0), ('context_days', -1), ('projects', [{'id': '../bad'}])]:
            with self.assertRaises(ValueError):
                configure({**self.config, key: value})
        with self.assertRaises(ValueError):
            configure({**self.config, 'machines': [{'name': 'same'}, {'name': 'same'}]})
        win = window(self.config, '2026-11-01', self.now)
        self.assertEqual(win['end'].timestamp() - win['start'].timestamp(), 25 * 3600)
