# Modified for Daybook core extraction; see UPSTREAM.md and LICENSE.
"""API-v1 opt-in extension hooks. No built-in remote source."""
from copy import deepcopy
import importlib
from pathlib import Path


class Reader:
    def __init__(self, config, boundary, folder, **kwargs):
        self.config, self.boundary, self.folder = config, boundary, Path(folder)
        self.plugins, self.data = [], {}
        # Gate every declared capability before importing any extension code.
        if any('network' in p.get('capabilities', []) for p in config['plugins']) and not config.get('_allow_network'):
            raise ValueError('Plugin requires explicit network consent')
        for item in config['plugins']:
            module, name = item['module'].split(':', 1)
            plugin = getattr(importlib.import_module(module), name)(deepcopy(item.get('options', {})))
            if getattr(plugin, 'api_version', None) != 1:
                raise ValueError('Unsupported plugin version')
            self.plugins.append(plugin)
        self.context = {'config': config, 'boundary': boundary, 'cache_dir': self.folder,
                        'allow_network': bool(config.get('_allow_network'))}

    def gather(self, phase, registry=None):
        repositories, evidence, standing, coverage = [], [], {}, {'failures': [], 'limits': []}
        for index, plugin in enumerate(self.plugins):
            result = plugin.sources({**self.context, 'phase': phase, 'registry': registry}) or {}
            repositories += result.get('repositories', [])
            evidence += self.boundary.tree(result.get('evidence', []))
            for label, value in self.boundary.tree(result.get('standing', {})).items():
                if label in standing:
                    raise ValueError('Multiple plugins supplied one standing snapshot')
                standing[label] = value
            coverage['failures'] += result.get('coverage', {}).get('failures', [])
            coverage['limits'] += result.get('coverage', {}).get('limits', [])
            if 'data' in result:
                self.data[str(index)] = self.boundary.tree(result['data'])
        coverage.update(repos_found=len(repositories), candidates_read=len(standing))
        return repositories, evidence, standing, coverage

    def finish(self, artifact):
        for index, plugin in enumerate(self.plugins):
            context = {**self.context, 'data': self.data.get(str(index), {}), 'win': self.context['win']}
            # Extensions never receive mutable core state by reference.
            sections = plugin.sections(context, deepcopy(artifact)) or []
            if not isinstance(sections, list) or any(not isinstance(s, str) for s in sections):
                raise ValueError('Plugin sections must be Markdown strings')
            artifact['markdown'] += ''.join('\n' + s.rstrip() + '\n' for s in sections)
            changed = plugin.postprocess(context, deepcopy(artifact))
            if changed is not None:
                if not isinstance(changed, dict) or not isinstance(changed.get('markdown'), str):
                    raise ValueError('Plugin postprocess must return an artifact')
                artifact = changed
        return artifact


def repos(reader, config):
    return reader.gather('discover')[0]


def collect(config, registry, repositories, win, reader):
    reader.context['win'] = win
    _, evidence, standing, coverage = reader.gather('evidence', registry)
    return evidence, standing, coverage
