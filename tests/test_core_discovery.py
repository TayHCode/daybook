# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Synthetic same-name files must not hide independent local sessions."""
from second_brain import sessions
from tests.daybook_fixtures import Case, claude, save


class DiscoveryTests(Case):
    def test_same_basename_in_different_folders_keeps_both_turns(self):
        save(self.root / 'claude/first/turn.jsonl', [claude('First request', uuid='first')])
        save(self.root / 'claude/second/turn.jsonl', [claude('Second request', uuid='second')])
        _, evidence, coverage = sessions.collect(self.config, self.registry, self.win,
                                                 self.boundary, self.root / 'cache')
        self.assertEqual(coverage['machine-a']['claude']['files_total'], 2)
        self.assertEqual(coverage['machine-a']['claude']['owner_messages_yesterday'], 2)
        self.assertEqual({e['text'] for e in evidence}, {'First request', 'Second request'})

    def test_overlapping_roots_do_not_scan_one_file_twice(self):
        save(self.root / 'claude/nested/turn.jsonl', [claude('One request', uuid='first')])
        self.config['machines'][0]['claude'].append(str(self.root / 'claude/nested'))
        _, evidence, coverage = sessions.collect(self.config, self.registry, self.win,
                                                 self.boundary, self.root / 'cache')
        self.assertEqual(coverage['machine-a']['claude']['files_total'], 1)
        self.assertEqual(len(evidence), 1)

    def test_explicit_repository_and_linked_worktree_are_supported(self):
        from second_brain import localgit
        from tests.daybook_fixtures import FakeGit
        repo = self.root / 'explicit-repo'
        (repo / '.git').mkdir(parents=True)
        self.config['local']['repo_roots'] = [str(repo)]
        found = localgit.discover(self.config, FakeGit())
        self.assertEqual([r['path'] for r in found], [str(repo.resolve())])
        linked = self.root / 'linked-worktree'
        linked.mkdir()
        (linked / '.git').write_text('gitdir: synthetic\n')
        self.config['local']['repo_roots'] = [str(linked)]
        found = localgit.discover(self.config, FakeGit())
        self.assertEqual([r['path'] for r in found], [str(linked.resolve())])
