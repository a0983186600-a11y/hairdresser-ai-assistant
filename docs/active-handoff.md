# Public B edition maintenance

## 2026-09-15 — OSS readiness

- Owner: Codex; status: local_verified_waiting_for_publication_approval.
- Scope: this public repository only. No commercial source, customer data,
  credentials, production adapters, deployment, or application submission.
- Baseline: 2a26d4369b461e8a8f9e39598b0e0f1b684d2977.
- Plan: CI; installed-wheel smoke check; accurate onboarding/architecture;
  contributor and feedback guidance; local verification evidence.
- Baseline checks: Python 3.14.3, 439 passed / 2 skipped; Ruff passed.
- Completed: CI matrix, isolated installed-wheel smoke script, three public
  distribution guards, contributor/security/trial guides and issue/PR templates;
  corrected stale tool counts and overbroad safety/production-parity claims.
- Verification: Python 3.12.12 and 3.14.3 each 442 passed / 2 skipped; Ruff passed;
  both installed wheels passed. Missing replay, missing CSS and extra worker
  mutations were rejected and restored. See maintenance-verification.md.
- Publication, release tags, user invitations and OSS application remain pending
  owner confirmation. Local checks are not GitHub Actions results.
- This maintenance branch starts from the public repository. Future exports
  must preserve these changes rather than overwrite the tree wholesale.
