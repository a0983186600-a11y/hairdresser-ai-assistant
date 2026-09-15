# Security and deployment boundaries

This is a local demo with synthetic data, not an authenticated multi-tenant SaaS.
Run it on loopback only. Do not expose it to the internet or supply live customer
data. The Docker example binds to `127.0.0.1` deliberately.

Server-injected scope, input validation, masking and tests reduce specific risks;
they are not proof that model text cannot hallucinate, that masked data is fully
anonymous, or that generated Python is securely isolated. The experimental
toolsmith must not be used with sensitive data or treated as an OS sandbox.

The public repository contains no production database adapter, production login,
LINE sender or real POS integration. Optional live model mode sends tool outputs
to the model endpoint you configure and may incur charges. Use synthetic data
and review that provider's terms before enabling it.

## Reporting

Do not post secrets, personal information or exploitable payloads in a public
issue. If GitHub's **Security → Report a vulnerability** is available on this
repository, use it. That feature's availability has not yet been confirmed; we
do not claim a private reporting channel is configured.

If no private report option is available, open only a minimal issue asking the
maintainer for a private contact, without vulnerability details or attachments.
There is no promised response-time SLA. If a key is exposed, revoke/rotate it at
its provider; deleting an issue or commit is not sufficient.

## Support status

The maintained target is the current `main` branch. There is no LTS or independent
security audit. Fixes and limitations should be documented with regression tests.
