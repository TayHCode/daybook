# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
from second_brain.projects import Registry, active_set
from tests.daybook_fixtures import Case


class ProjectTests(Case):
    def registry_for(self, repos=None, local=None):
        return Registry(self.config, repos or [{'full_name': 'Owner/Demo-App', 'name': 'Demo-App'}], local or [], self.boundary)

    def test_all_path_shapes_and_exact_names(self):
        registry = self.registry_for()
        for path in ['/synthetic/user/.t3/worktrees/demo-app/t3code-1', '/synthetic/user/worktrees/demo-app/feature-x',
                     'C:\\Synthetic\\user\\OneDrive\\Desktop\\REPOS\\Demo-App\\src', '/synthetic/mac-user/Desktop/REPOS/demo-app']:
            self.assertEqual(registry.resolve(path, 'machine-a'), 'Demo-App', path)
        self.config['projects'] += [{'id': 'widget-yard', 'aliases': ['widget-yard']}, {'id': 'lark', 'aliases': ['lark']}]
        registry = self.registry_for()
        self.assertNotEqual(registry.resolve('/synthetic/user/projects/runtime/widget-yard/x', 'machine-a'), 'widget-yard')
        self.assertNotEqual(registry.resolve('/synthetic/user/projects/lark-kit', 'machine-a'), 'lark')

    def test_private_names_overrides_uniqueness_and_local_map(self):
        repos = [{'full_name': 'Owner/SYNTHETIC_PRIVATE', 'name': 'SYNTHETIC_PRIVATE'}]
        registry = self.registry_for(repos)
        self.assertTrue(registry.labels()[0].startswith('project-'))
        self.config['projects'] = [{'id': 'safe', 'repo': 'Owner/SYNTHETIC_PRIVATE'}]
        local = [{'path': '/local/root', 'folder': 'local', 'remote': 'Owner/SYNTHETIC_PRIVATE',
                  'worktrees': [{'path': '/synthetic/user/projects/other'}]}]
        registry = self.registry_for(repos, local)
        self.assertEqual(registry.resolve('/synthetic/user/projects/other/src', 'machine-a'), 'safe')
        self.assertNotEqual(registry.resolve('/synthetic/user/projects/other/src', 'machine-b'), 'safe')
        registry._add('safe')
        self.assertEqual(len(registry.labels()), len(set(registry.labels())))

    def test_active_rules_agent_only_parked_dormant(self):
        self.config['projects'] = [{'id': n, 'pin': n == 'pinned'} for n in ['commit', 'pr', 'owner', 'pinned', 'agent', 'parked', 'dormant']]
        registry = Registry(self.config, [], [], self.boundary)
        facts = {n: {'standing': {'days': {}}} for n in registry.labels()}
        for label, metric in [('commit', 'activity'), ('pr', 'prs'), ('owner', 'owner_messages'), ('agent', 'agent_user_messages')]:
            facts[label]['standing']['days']['2026-10-07'] = dict([(metric, 1)])
        facts['parked']['standing']['worktrees'] = [{'modified': 1}]
        active, worked, parked, dormant = active_set(registry, facts, self.win, self.config)
        self.assertEqual(set(active), {'commit', 'pr', 'owner', 'pinned'})
        self.assertEqual(set(worked), {'commit', 'pr', 'owner'})
        self.assertEqual(parked, ['parked'])
        self.assertEqual(dormant, 2)
