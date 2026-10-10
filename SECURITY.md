# Security

Security fixes target the current 0.1 release. Older versions are unsupported;
update before reporting a vulnerability.

Treat transcripts, git subjects and plugin output as untrusted input. Keep configuration,
selected evidence and generated briefs private; the default boundary is not a scrubber.
Plain mode reads explicit local sources only. Model calls and network plugins require
per-run consent. A trusted installed plugin is arbitrary code and has your privileges.
Do not place tokens in repository files or submit real transcripts in bug reports.

Report security issues through a private reporting channel provided by the distributor.
If none exists, contact the distributor privately before sharing details. Include
versions, a synthetic reproduction and impact; omit credentials and private evidence.
Never open a public issue containing an exploitable secret. This source distribution
does not provide a hosted security contact.
