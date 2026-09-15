# 15-minute public demo trial

This trial checks whether a new person can use the public project. It is not a
testimonial request and does not require a star, PR, account or model key.

## Start

Follow README's zero-key instructions. Open `http://127.0.0.1:8100` on the same
computer. All customers, revenue and conversations are synthetic. Do not enter
real customer information. No booking or message will reach a salon or LINE.

## Try

1. Open the homepage. Can you tell this is a demo?
2. Click a suggested question. Read the answer and expand its tool trace. Can you
   tell where the numbers came from?
3. Try another suggested question about customers or spending.
4. Search for a synthetic customer, open the booking panel and inspect the
   schedule. Demo changes are temporary and separate from the analytical dataset.
5. Ask an unrecorded question. The zero-key edition should say it has no recording,
   not pretend that the model is answering live.

Replay answers are prerecorded. Tool calls execute again over the fixed dataset.
Do not evaluate general conversation quality from this mode; testing a live model
is a separate, optional, potentially paid exercise.

## Feedback to keep (only with permission)

For a hands-on trial beyond prerecorded answers, try the
[editable sample workflow](../examples/README.md). Change a fake amount and check
that the actual tool result changes. No model key is needed; this command-line
workflow is separate from the web demo's fixed data and recordings.

- Date and repository commit/tag; operating system; chosen install method.
- Which steps worked? Where did you get stuck? Exact error with secrets removed.
- Was a tool result understandable or useful? What task remains unsolved?
- Optional screenshot using only the shipped fake data.
- May the maintainer publish this feedback? Default: **no**. Anonymous feedback
  is fine; decline screenshots or attribution without affecting participation.

Send a public issue only if you consent to publishing its contents. Use the bug
template for technical failures. Do not paste `.env`, customer records or keys.
Maintainers should record the real fix and retest, not rewrite criticism into
endorsements. Trial participants, unique clones and active users are different
metrics; report each honestly.
