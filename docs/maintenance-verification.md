# Public maintenance verification — 2026-09-15

Scope: public B edition, branch `codex/oss-readiness`, based on
`2a26d4369b461e8a8f9e39598b0e0f1b684d2977`. These are **local macOS results**,
not GitHub Actions badges, production validation or live-model quality scores.

## Before and after

| Check | Original public baseline | Maintenance tree |
| --- | --- | --- |
| Python 3.14.3, full pytest | 439 passed, 2 skipped | 442 passed, 2 skipped |
| Python 3.12.12, full pytest | Not rerun at baseline | 442 passed, 2 skipped |
| Ruff | All checks passed | All checks passed |
| Installed wheel, Python 3.12 + 3.14 | Not measured this round | Both passed |

Commands: `uv sync --locked --extra dev`,
`uv run --locked pytest -q -p no:cacheprovider`, `uv run --locked ruff check .`.
Wheel check: `uv build --wheel`, install the wheel (not editable) into a fresh
venv, run `python -I scripts/check_installed.py` using that venv's interpreter.

The wheel check uses an empty working directory and blocks outbound socket
connections in the application process. It verifies demo health, eight recorded
questions with actual tool traces, static assets, an unrecorded-question fallback
and refusal to enter production mode. This is an ASGI application check, not a
Docker/network-server test or a security sandbox certification.

Source environments use the committed lockfile. The wheel environments resolve
runtime dependencies as a fresh installer would; observed uvicorn was 0.53.0
there versus 0.52.4 in the source lockfile. Neither uses a model API key.

## Deliberate breakage checks

1. Move the installed wheel's `assistant/replay` directory aside: the smoke check
   fails at `health["replay_available"] is True`. Restored afterwards.
2. Move installed `frontend/app.css` aside: the smoke check fails with
   `AssertionError: app.css`. Restored afterwards.
3. Add `worker` to demo compose: the new structural test fails, showing
   `{'assistant', 'worker'} != {'assistant'}`. Restored byte-for-byte afterwards.

These checks demonstrate that the relevant guards reject those specific defects;
they do not prove the absence of every packaging, concurrency or security bug.

## Known limits / follow-up

- Two skips: optional paid live-model test and private exam comparison. Not passes.
- Two dependency deprecation warnings (FastAPI/Starlette TestClient and AnyIO).
- Build warns about the legacy TOML license table; migration remains follow-up.
- The new Ubuntu CI matrix is configured but **has not run on GitHub** until this
  branch is published and Actions executes it. No Docker service was started.
- No new release/tag, hosted deployment, external trial or OSS application sent.
- No evidence of external adoption collected in this maintenance batch. No stars,
  clones or private SaaS activity counted as public-project usage.
- Before release: run remote CI, review changes, check packaging, then obtain owner
  approval for the release. Preserve the original submission tag.
