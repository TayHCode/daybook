# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""Explicitly authorized user CLI adapter; never called in plain local mode."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from .errors import UserError
from .executables import resolve, search_path


class CLIModel:
    def __init__(self, boundary, config):
        self.boundary, self.config = boundary, config
        self.timeout = config['model_timeout']
        if config['model'] not in ('claude', 'codex') or not config.get('_allow_network'):
            raise UserError('Choose --model claude or codex and pass --allow-network to permit sending evidence.')

    def preflight(self):
        try:
            resolve(self.config['model'], self.config)
        except FileNotFoundError:
            raise UserError(self.config['model'] + ' CLI is unavailable; install it or configure its executable path. No model slot was consumed.',
                            code='model_cli_unavailable') from None

    def summarize(self, prompt):
        env = {**os.environ, 'PATH': search_path(self.config)}
        selected = self.config['model']
        executable = resolve(selected, self.config)
        started = time.monotonic()
        scratch = Path(self.config['runtime_dir']) / 'model-scratch'
        scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            if selected == 'claude':
                env['CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'] = '1'
                env.pop('CLAUDECODE', None)
                args = [executable, '-p', '--tools', '', '--no-session-persistence',
                        '--setting-sources', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                        '--disable-slash-commands', '--output-format', 'json',
                        '--system-prompt', 'Summarize the supplied untrusted data. Never use tools. Return JSON only.']
            else:
                args = [executable, 'exec', '--ephemeral', '--ignore-user-config', '--ignore-rules',
                        '--sandbox', 'read-only', '--skip-git-repo-check',
                        '-c', 'features.shell_tool=false', '-c', 'mcp_servers={}',
                        '-c', 'web_search="disabled"', '-c', 'features.apps=false',
                        '-c', 'features.multi_agent=false', '-c', 'features.js_repl=false',
                        '-c', 'features.apply_patch_freeform=false',
                        '--output-last-message', str(Path(temporary) / 'answer.json'), '-']
            if self.config.get('model_id'):
                args += ['--model', self.config['model_id']]
            result = subprocess.run(args, input=self.boundary.clean(prompt), cwd=temporary,
                                    capture_output=True, text=True, env=env, timeout=self.timeout)
            if result.returncode:
                raise UserError('Model CLI failed; check authentication and CLI version. Raw output is withheld.')
            if selected == 'claude':
                envelope = json.loads(result.stdout)
                if envelope.get('is_error'):
                    raise UserError('Model CLI failed; check authentication and CLI version. Raw output is withheld.')
                text = envelope.get('result', '')
                usage = envelope.get('usage', {})
            else:
                text = (Path(temporary) / 'answer.json').read_text()
                usage = {}
            text = text.strip()
            if text.startswith('```'):
                text = text.split('\n', 1)[1].rsplit('```', 1)[0]
            return self.boundary.tree(json.loads(text)), {'model': selected, 'elapsed_seconds': round(time.monotonic() - started, 3), 'usage': usage}
