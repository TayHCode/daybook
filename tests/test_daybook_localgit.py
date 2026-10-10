# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
from second_brain import localgit
from second_brain.projects import Registry
from second_brain.daybook import build_facts, fallback_start
from second_brain.projects import active_set
from second_brain.storage import write
from unittest.mock import patch
from tests.daybook_fixtures import Case, FakeGit


class LocalGitTests(Case):
    def test_no_upstream_is_not_unpushed_or_parked_in_cached_snapshot(self):
        registry = Registry(self.config, [], [], self.boundary)
        refs = [{'name': f'local-{i}', 'upstream': '', 'ahead': 0} for i in range(115)]
        state = {'demo': {'branches': refs, 'unpushed_branches': refs, 'worktrees': []}}
        cache = self.root / 'snapshot.json'
        write(cache, {'evidence': [], 'standing': state, 'coverage': {}})
        with patch('second_brain.localgit.git', side_effect=AssertionError('cache must stay local')):
            _, standing, _ = localgit.collect(self.config, registry, self.win, self.boundary, cache)
        self.assertEqual(standing['demo']['unpushed_branches'], [])
        self.assertEqual(len(standing['demo']['branches_without_upstream']), 115)
        facts = build_facts(registry, [], [], {}, standing, self.win)
        self.assertEqual(active_set(registry, facts, self.win, self.config)[2], [])
        # An absent upstream does not prove unpublished commits.
        self.assertFalse(fallback_start(facts['demo'])['step'].startswith('Push'))
        refs.append({'name': 'ahead', 'upstream': 'origin/ahead', 'ahead': 2})
        localgit._branch_tracking(state)
        facts = build_facts(registry, [], [], {}, state, self.win)
        self.assertEqual(active_set(registry, facts, self.win, self.config)[2], ['demo'])
        self.assertEqual(facts['demo']['standing']['unpushed_branches_total'], 1)

    def test_readonly_guards_and_remote_mapping(self):
        fake = FakeGit()
        localgit.git(['config', '--get', 'remote.origin.url'], '/synthetic', self.config, fake)
        with self.assertRaises(AssertionError):
            localgit.git(['fetch'], '/synthetic', self.config, fake)
        with self.assertRaises(AssertionError):
            localgit.git(['worktree', 'remove'], '/synthetic', self.config, fake)
        self.assertEqual(fake.calls[0][:5], [self.config['executables']['git'], '--no-optional-locks', '-C', '/synthetic', 'config'])
        for url in ['https://github.com/Owner/demo.git', 'git' + '@' + 'github.com:Owner/demo.git', 'ssh://git' + chr(64) + 'github.com/Owner/demo.git']:
            self.assertEqual(localgit.remote_name(url), 'Owner/demo')

    def test_parsers(self):
        repo = {'path': '/synthetic'}
        def output(text):
            from types import SimpleNamespace
            return lambda *a, **k: SimpleNamespace(returncode=0, stdout=text)
        trees = localgit.worktrees(repo, self.config, output('worktree /synthetic\nHEAD ' + 'a' * 40 + '\nbranch refs/heads/main\n\nworktree /other\nHEAD ' + 'b' * 40 + '\ndetached\nprunable gone\n'))
        self.assertEqual(trees[0]['branch'], 'main')
        self.assertTrue(trees[1]['detached'] and trees[1]['prunable'])
        refs = localgit.branches(repo, self.config, output('feature\x1fabc\x1f2026-10-07T12:00:00Z\x1forigin/feature\x1f[ahead 3, behind 2]\n'))
        self.assertEqual((refs[0]['ahead'], refs[0]['behind']), (3, 2))
        state = localgit.status('/synthetic', self.config, output('# branch.head main\n1 .M N... 100644 100644 100644 abc def file name.txt\n? new file.txt\n'))
        self.assertEqual((state['modified'], state['untracked']), (1, 1))
        self.assertEqual(state['paths'], ['file name.txt', 'new file.txt'])
        rows = localgit.commits(repo, self.win, self.config, output('a' * 40 + '\x1f2026-10-07T12:00:00Z\x1fExample\x1fChange\x1e\n 2 files changed, 5 insertions(+), 3 deletions(-)\n'))
        self.assertEqual((rows[0]['files'], rows[0]['additions'], rows[0]['deletions']), (2, 5, 3))

    def test_snapshot_cache_and_refresh(self):
        registry = Registry(self.config, [], [{'path': '/synthetic', 'folder': 'local', 'remote': None}], self.boundary)
        fake = FakeGit({('worktree', 'list', '--porcelain'): 'worktree /synthetic\nHEAD abc\nbranch refs/heads/main\n'})
        cache = self.root / 'snapshot.json'
        localgit.collect(self.config, registry, self.win, self.boundary, cache, run=fake)
        count = len(fake.calls)
        localgit.collect(self.config, registry, self.win, self.boundary, cache, run=fake)
        self.assertEqual(len(fake.calls), count)
        localgit.collect(self.config, registry, self.win, self.boundary, cache, refresh=True, run=fake)
        self.assertGreater(len(fake.calls), count)
