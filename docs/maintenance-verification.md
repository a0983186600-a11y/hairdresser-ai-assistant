# Public maintenance verification — 2026-09-15

## Published branch: Ubuntu verification

Owner-approved branch publication only; `main` and the original submission tag
remain unchanged. Product commit `24a77dd` passed
[GitHub run 34972346855](https://github.com/a0983186600-a11y/hairdresser-ai-assistant/actions/runs/34972346855):
Ubuntu Python 3.12 and 3.14 each **476 passed / 2 skipped**, Ruff green and the
installed-wheel check plus local-data CLIs passed.

First run `34971996364` at `889e506` failed one test on both Linux versions:
an infinite loop was terminated with returncode -9 at about five seconds, but
the test only allowed SIGXCPU or a wall timeout. The reporting fix preserves
the CPU soft/hard pair, memory cap, wall timeout and code restrictions. SIGKILL
is reported as `killed` with signal 9; its underlying cause is explicitly unknown,
not assumed to be CPU exhaustion. Ordinary non-signal failures remain `crashed`.
New classification test was red before the fix. The real infinite-loop test
still requires failure and termination within the same wall-clock budget.

GitHub reports deprecation warnings for Node 20 action runtimes (forced to Node
24); the pinned actions completed successfully. Updating those action versions
is follow-up, not a suppressed failure. No paid model or production validation
was performed. This is not an application acceptance or external-user trial.

## Follow-up: editable samples and dataset validation

Base `781f9ae`, same `codex/oss-readiness` branch. Python 3.14.3 and 3.12.12 final
full suites each **472 passed, 2 skipped** (16.58s / 16.80s); Ruff passed.
Both installed-wheel replay checks passed after installing the updated package.
The installed CLI validated the synthetic/empty data and ran the customer
follow-up workflow without a model key. Runtime code matches the source tree.

- Initial four tests on the unmodified provider: **4 failed** (owner mismatches
  in visits, conversations and appointments; duplicate customer IDs).
- Fixed at ingestion, not separately in each query. Existing shipped data remains
  unchanged. First schema attempt missed the existing optional `pos_customer_id`
  field; it was added explicitly, not by allowing arbitrary extra fields.
- Bypass the owner comparison: **3 failed**. Bypass duplicate-ID check: **5 failed**.
  Restore both and rerun both full suites. A combined restoration patch initially
  failed to apply; the file was inspected and restored with separate patches before
  continuing. No mutation remained in the final tree.
- New tests cover malformed schema, negative/string/boolean amounts, unknown
  references, timezone-less timestamps, sensitive values not echoed in errors,
  duplicate JSON fields, bounded file reads, CLI exit status, empty results,
  scope isolation and changed data producing changed results instead of replay.
- Hand-authored example: 2 designers, 3 customers, 4 visits, 1 appointment,
  1 conversation. The tutorial yields one 92-day inactive customer, known spend
  3000 TWD plus one unknown-amount visit, and a hair-color follow-up draft.
  Changing 3000 to 3500 changes the tool answer; making the last visit recent
  yields an empty list and no draft. Nothing is sent.
- Examples ship in the repository; they do not replace the server's existing
  fixture/replay assets. No CSV/SQL/POS/LINE importer or UI upload added.
- No real data, paid model call, Docker start, remote CI run, push, release or
  application submission. Same two optional skips and dependency warnings remain.

## Prior maintenance batch

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
