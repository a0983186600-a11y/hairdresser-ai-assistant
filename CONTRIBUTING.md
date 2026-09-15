# Contributing

This repository is the public, synthetic-data edition of got you. Contributions
to installation, reproducible bug fixes, tool contracts, privacy tests and
documentation are welcome. You do not need a paid model account to start.

## Local checks

Use Python 3.12 or 3.14 and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --locked --extra dev
uv run --locked pytest -q -p no:cacheprovider
uv run --locked ruff check .
```

The live-model test requires a key and the private-exam comparison requires a
file not distributed here; skips are not passes. Do not add keys to CI.

For a bug fix, first add a failing regression test, then the smallest fix. Include
the reproduction, before/after result and checks in your PR. Do not weaken a
privacy or scope guard to make a test pass. An issue is helpful for larger changes
but not required for a small correction. We do not require artificial PR counts.

## Installed-package check

Source tests alone do not prove the wheel contains its frontend, fixtures and
replay recordings. In a new temporary directory/venv (do not reuse an editable
installation):

```sh
uv build --wheel
CHECK_DIR="$(mktemp -d)"
uv venv "$CHECK_DIR/venv" --python 3.12
uv pip install --python "$CHECK_DIR/venv/bin/python" dist/*.whl
"$CHECK_DIR/venv/bin/python" -I "$PWD/scripts/check_installed.py"
```

The check changes into an empty directory, blocks outbound socket connections,
and verifies the health response, eight recorded questions, referenced assets,
unknown-question fallback and production-mode refusal. It uses the real ASGI
application and tools; it does not call a paid model. Keep only one release wheel
in `dist/` when using the wildcard above. Remove your temporary directory yourself
after inspection if no longer needed.

## Boundaries

- Use synthetic data only. No customer transcripts, names, telephone numbers,
  credentials, private adapters or commercial git history in issues or PRs.
- Scope and time remain server controlled; tools must not select another owner.
- Demo bookings and messages never write to a real POS or send LINE messages.
- Proposed Python tools are a local demo experiment, not a production sandbox.
- Do not claim a new provider/model is compatible without recording the tested
  model, configuration, date and failures, with credentials removed.
- Maintainers review changes before merging. This repo does not deploy the
  commercial platform. Future source exports must merge, not erase, public-only
  maintenance, CI and community documentation.

See [SECURITY.md](SECURITY.md) for sensitive reports. See
[the trial guide](docs/try-it.md) for a short non-technical exercise.
