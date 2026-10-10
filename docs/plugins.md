# Local plugin API, version 1

Plugins are separately installed Python packages. No auto-discovery, path injection
or bundled private plugin exists. A private config can opt in:

```json
{"plugins": [{"module": "example_plugin:Plugin", "capabilities": [], "options": {}}]}
```

A plugin constructor receives a copy of `options` and declares `api_version = 1`.
It implements these three methods:

- `sources(context)` runs with `phase="discover"` to supply a repository catalog,
  then `phase="evidence"` with the resolved project registry to supply evidence.
  Return an object with optional `repositories`, `evidence`, `standing`, `coverage`
  and `data`. Evidence uses `id`, `project` (a registry label), `source`, `event_at`
  (ISO timestamp with offset), `period` (`context`, `yesterday`, `today`), `kind`,
  `verification` and `text`. Git commit kinds also carry `sha` and `subject`.
  Repository catalog entries use `name`, `full_name`, `default_branch`, optional
  `fork`/`archived`. `standing` maps labels to existing brief fact schemas.
  `coverage` supplies `failures` and `limits`. `data` is opaque JSON for this plugin;
  it is scrubbed and fingerprinted but never enters a core model prompt.
- `sections(context, artifact)` receives copies of the core artifact and this plugin's
  `data`; returns a list of Markdown strings appended to the brief. No hooks execute
  on an identical cached rerun, so keep output deterministic for supplied data.
- `postprocess(context, artifact)` receives a copy including appended sections;
  returns a replacement artifact or `None`. It can add private fields/angles.
  It must return a dictionary with string `markdown`. Final output is scrubbed again.

Context contains `config`, `boundary`, `cache_dir`, `win` (calendar window),
`allow_network`, `phase`/`registry` for sources, and `data` for output hooks.
No model client is handed to a plugin implicitly. Keep plugin caches under its own
subfolder. Errors surface as coverage failures for collection; output errors fail
without writing an incomplete artifact. Two source plugins cannot silently overwrite
one another's standing snapshot for the same project.

Declare `"capabilities": ["network"]` for any network access, including read-only
account APIs or model post-processing. The core rejects that selection **before
import** unless `brief --allow-network` was supplied for the run. Declare capabilities
honestly. Trusted plugins can perform arbitrary Python actions; the core cannot enforce
network isolation against a plugin that lies. Keep options containing account names,
paths, identities and tokens in private configuration, never in the public tree.

For migration, use a separate new state directory. Existing state/cache files and
operational consumers are not automatically migrated by this API.
