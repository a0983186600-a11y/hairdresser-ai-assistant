# Changelog

## Unreleased

- Report Linux hard-killed subprocesses as `killed` with the observed SIGKILL,
  rather than claiming an ordinary crash or an unproven CPU cause. Resource
  limits are unchanged; the real infinite-loop test still requires termination
  within the wall-clock budget.

- Add a small hand-authored synthetic dataset, five empty table templates and a
  deterministic inactive-customer → history → follow-up draft walkthrough.
- Validate custom input schema, identifiers and customer-owner relationships
  before constructing the demo provider; reject duplicate IDs instead of silent
  overwrites. CLI reports errors without printing supplied values. This is not
  an anonymization tool or a production-data import feature.

- Add zero-key CI for Python 3.12 and 3.14: tests, Ruff and an installed-wheel
  smoke check outside the checkout, with network access blocked during the check.
- Document contributor workflow, a synthetic-data trial, security boundaries and
  feedback templates. No external users or adoption claims are implied.
- Correct tool-count drift and distinguish the original hackathon submission
  from later maintenance. Clarify that model wording may still hallucinate and
  the experimental toolsmith is not a production security sandbox.
- No production functionality, customer data or private git history imported.

## buildmode-2026-submission

Original public submission, commit
`2a26d4369b461e8a8f9e39598b0e0f1b684d2977`. This existing tag is not changed.
It included the synthetic-data assistant, demo workbench, replay and evaluation
fixtures. Historical submission claims are not a live production audit.
