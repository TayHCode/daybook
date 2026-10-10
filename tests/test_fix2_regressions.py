# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Synthetic repository, symlink and coarse-timestamp regression probes."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from second_brain import cli, daybook, localgit, sessions
from second_brain.demo import generate
from second_brain.model import CLIModel
from second_brain.projects import Registry
from second_brain.storage import read, write
from tests.daybook_fixtures import Case, FakeGit, claude, save
from tests.test_daybook_run import RunFixture


class RepositoryTests(Case):
    def make_repo(self):
        self.config['executables']['git'] = shutil.which('git')
        self.config['projects'] = []
        self.repo = self.root / 'repos/lantern-api'
        self.repo.parent.mkdir()
        # Reuse the existing neutral demo commit rather than invent attribution.
        seed = self.root / 'seed'
        generate(seed)
        (seed / 'repos/sample-app').rename(self.repo)
        self.linked = self.repo.parent / 'lantern-api-retry'
        env = {**os.environ, 'GIT_AUTHOR_NAME': 'Release Export',
               'GIT_AUTHOR_EMAIL': 'release-export', 'GIT_COMMITTER_NAME': 'Release Export',
               'GIT_COMMITTER_EMAIL': 'release-export', 'GIT_AUTHOR_DATE': '2026-10-07T12:00:00Z',
               'GIT_COMMITTER_DATE': '2026-10-07T12:00:00Z'}
        def git(*args):
            subprocess.run([self.config['executables']['git'], '-C', str(self.repo), *args],
                           env=env, check=True, capture_output=True)
        git('branch', '-m', 'main')
        other = self.repo.parent / 'atlas-notes'
        shutil.copytree(self.repo, other)
        git('worktree', 'add', '-b', 'feature/retry', str(self.linked))

    def test_main_and_linked_selection_share_one_project_and_session_heading(self):
        self.make_repo()
        save(self.root / 'claude/main.jsonl', [claude('Main checkout request', cwd=str(self.repo), uuid='main')])
        save(self.root / 'claude/retry.jsonl', [claude('Linked checkout request', cwd=str(self.linked), uuid='retry')])
        for index, roots in enumerate(([self.repo.parent], [self.linked], [self.linked, self.repo],
                                       [self.repo, self.linked])):
            with self.subTest(roots=index):
                config = {**self.config, 'runtime_dir': str(self.root / f'run-{index}'),
                          'local': {**self.config['local'], 'repo_roots': list(map(str, roots))}}
                result = daybook.run(config, self.boundary, None, now=self.now, no_model=True)
                artifact = read(Path(result['path']).with_suffix('.json'))
                self.assertEqual(artifact['active'], ['lantern-api'])
                self.assertEqual(artifact['facts']['lantern-api']['yesterday']['owner_messages_total'], 2)
                self.assertIn('### lantern-api\n', artifact['markdown'])
                self.assertNotIn('### lantern-api-retry', artifact['markdown'])
                state = artifact['facts']['lantern-api']['standing']['worktrees']
                self.assertEqual({w['branch'] for w in state}, {'main', 'feature/retry'})
                self.assertEqual(artifact['coverage']['localgit']['worktrees'], 3 if index == 0 else 2)
                self.assertEqual(artifact['coverage']['localgit']['repos'], 2 if index == 0 else 1)

    def test_duplicate_symlink_roots_and_local_routing_are_canonical(self):
        self.make_repo()
        alias = self.root / 'alias'
        alias.symlink_to(self.repo.parent, target_is_directory=True)
        self.config['local']['repo_roots'] = [str(alias), str(self.repo.parent), str(self.repo)]
        repos = localgit.discover(self.config)
        self.assertEqual(len(repos), 2)
        for repo in repos:
            repo['worktrees'] = localgit.worktrees(repo, self.config)
        registry = Registry(self.config, [], repos, self.boundary)
        self.assertEqual(registry.resolve(str(alias / 'lantern-api/src'), 'machine-a'), 'lantern-api')
        self.assertEqual(registry.resolve(str(alias / 'lantern-api-retry/src'), 'machine-a'), 'lantern-api')
        self.assertEqual(registry.routing('lantern-api')['local_path'], str(self.repo.resolve()))

    def test_discovery_paths_use_canonical_folder_names(self):
        repo = self.root / 'main-repo'
        (repo / '.git').mkdir(parents=True)
        alias = self.root / 'different-name'
        alias.symlink_to(repo, target_is_directory=True)
        self.config['local']['repo_roots'] = [str(alias), str(repo)]
        found = localgit.discover(self.config, FakeGit())
        self.assertEqual([(r['path'], r['folder']) for r in found], [(str(repo.resolve()), 'main-repo')])


class SourceTests(RunFixture):
    def test_probe_hash_covers_small_files_and_large_head_tail_and_interior(self):
        for size in (1024, 4 * 1024 * 1024):
            path = self.root / f'probe-{size}'
            path.write_bytes(b'a' * size)
            frozen = path.stat()
            real_stat = Path.stat
            def coarse_stat(value, *args, **kwargs):
                return frozen if value == path else real_stat(value, *args, **kwargs)
            with patch.object(Path, 'stat', coarse_stat):
                for offset in (0, size // 4, size // 2, 3 * size // 4, size - 1):
                    before = sessions._source_stamp(path)
                    with path.open('r+b') as handle:
                        handle.seek(offset)
                        handle.write(b'b')
                    self.assertNotEqual(sessions._source_stamp(path), before)
                    with path.open('r+b') as handle:
                        handle.seek(offset)
                        handle.write(b'a')

    def test_same_size_rewrite_with_frozen_stat_refreshes_without_model_calls(self):
        save(self.path, [claude('Synthetic request alpha', uuid='same-turn')])
        frozen = self.path.stat()
        real_stat = Path.stat
        # resolve() itself uses stat on older Python, so compare lexical paths here.
        def coarse_stat(path, *args, **kwargs):
            stat = real_stat(path, *args, **kwargs)
            if str(path) != str(self.path):
                return stat
            return SimpleNamespace(st_size=stat.st_size, st_mtime=frozen.st_mtime,
                st_mtime_ns=frozen.st_mtime_ns, st_ctime_ns=frozen.st_ctime_ns,
                st_dev=frozen.st_dev, st_ino=frozen.st_ino, st_mode=stat.st_mode)
        with patch.object(Path, 'stat', coarse_stat):
            self.run_book()
            save(self.path, [claude('Synthetic request bravo', uuid='same-turn')])
            os.utime(self.path, ns=(frozen.st_mtime_ns, frozen.st_mtime_ns))
            self.assertEqual(self.path.stat().st_size, frozen.st_size)
            result = self.run_book()
            self.assertEqual((result['status'], result['calls']), ('refreshed', 0))
            self.assertIn('Synthetic request bravo', json.dumps(self.artifact()['facts']))
            self.assertEqual(self.run_book()['status'], 'unchanged')

    def test_symlink_root_forms_preserve_discovery_and_cached_identity(self):
        alias = self.root / 'source-alias'
        alias.symlink_to(self.path.parent, target_is_directory=True)
        config = {**self.config, 'machines': [{'name': 'machine-a', 'claude': [str(alias), str(self.path.parent)]}]}
        found, cov = sessions.discover(config, self.win['lookback_start'], self.win['now'])
        self.assertEqual(cov['machine-a']['claude']['files_total'], 1)
        self.assertEqual(found[0]['path'], self.path.resolve())
        self.assertEqual(found[0]['root'], self.path.parent.resolve())
        first = daybook.run(config, self.boundary, self.model, now=self.now)
        self.assertEqual(first['calls'], 2)
        before = self.artifact()['facts']
        config['machines'][0]['claude'] = [str(self.path.parent)]
        # Source selection changes config, but canonical source references must not change.
        daybook.run(config, self.boundary, self.model, now=self.now)
        self.assertEqual(self.artifact()['facts'], before)
        _, evidence, _ = sessions.collect(config, self.registry, self.win, self.boundary, self.root / 'separate-cache')
        self.assertEqual({e['text'] for e in evidence}, {'Build the synthetic feature', 'A synthetic handoff'})


class DiagnosticTests(RunFixture):
    def test_missing_cli_reason_is_once_for_multiple_projects_and_retries(self):
        for label in ('other', 'third'):
            self.config['projects'].append({'id': label, 'aliases': [label]})
            save(self.root / f'claude/{label}.jsonl', [claude(label, cwd=f'/synthetic/user/projects/{label}')])
        self.config.update(model='claude', executable_path=[], executables={})
        model = CLIModel(self.boundary, self.config)
        for _ in range(2):
            with patch.object(model, 'preflight', wraps=model.preflight) as preflight:
                result = daybook.run(self.config, self.boundary, model, now=self.now)
            self.assertEqual(result['calls'], 0)
            self.assertEqual(preflight.call_count, 1)
            self.assertEqual(self.artifact()['markdown'].count('claude CLI is unavailable'), 1)
            self.assertEqual(read(self.root / 'runtime/calls.json', {}), {})

    def test_doctor_names_missing_repo_root_with_failure_status(self):
        missing = self.root / 'missing-repository'
        config = {**self.config, 'local': {**self.config['local'], 'repo_roots': [str(missing)]}}
        config_path = self.root / 'config.json'
        write(config_path, config)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(cli.main(['doctor', '--config', str(config_path)]), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['repo_roots_missing'], 1)
        self.assertEqual(result['missing_repo_roots'], [str(missing)])
