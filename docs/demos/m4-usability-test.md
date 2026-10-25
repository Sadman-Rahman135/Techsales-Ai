# M4 gate: usability session script

**Gate (plan §15 M4):** "A person unfamiliar with code can upload a doc and use the assistant."
This needs a real person, so it isn't ticked until a session like this one has been run and written up below.
About 20 minutes per participant; two or three participants are enough to find the big problems.

## Before the session (facilitator)

1. Start a clean copy of the app: `backend/.venv/Scripts/python scripts/usability_session.py up --gpu` (leave out
   `--gpu` without an NVIDIA card). It stops the everyday stack (its data is kept), starts the release images with
   empty data and the local models, seeds the users and writes the task files. The participant sees no leftovers.
2. The participant's sign-in (a fictional manager account, this session only) is in
   `data/usability/session-credentials.txt`. Every document is fictional; don't use anyone's real data.
3. Put the two task files on the desktop: `data/demo-docs/Gift-Wrapping-Policy.pdf` (task 2) and
   `data/demo-docs/Gift-Wrapping-Policy-recycled.pdf` (task 5).
4. Open the browser on `http://127.0.0.1:13000/login`, logged out. Close developer tools. Have a timer and this
   sheet ready.
5. Afterwards: `backend/.venv/Scripts/python scripts/usability_session.py down --gpu` deletes the session's data and
   starts the everyday stack again.

Tell the participant: *"We're testing the app, not you. Think aloud. I can't help during a task, but I'll answer
everything afterwards. You can stop at any time."* Ask before you record the screen.

## Tasks (read each one aloud; don't name buttons or pages)

| # | Task | Success when | Time limit |
| --- | --- | --- | --- |
| 1 | "You're the store manager. Sign in with this email and password." | Lands on the staff pages | 2 min |
| 2 | "Add the gift-wrapping policy so customers can ask about it." | Document uploaded **and published** (status Published) | 5 min |
| 3 | "Pretend you're a customer. Ask the assistant whether gift wrapping is free." | Gets the answer with a citation, and opens the source | 3 min |
| 4 | "Ask the assistant something the shop's documents don't cover, like whether TechCare sells bicycles." | Recognises the "Not in our documents" reply as a "don't know", not an error | 2 min |
| 5 | "The policy changed: wrapping now uses recycled paper. Update it." | Uploads a new version and publishes it; old version Archived | 5 min |
| 6 | "Check that customers now get the new rule." | Asks again and sees version 2 cited | 3 min |

## What to write down per task

Completed (yes / with a hint / no) · time · where they hesitated or clicked something wrong · what they said · any
error message they saw and whether they understood it.

## After the tasks (ask, then note the answers)

1. On a scale of 1–5, how easy was adding a document? What was confusing?
2. When the assistant said it couldn't find something, what did you think had happened?
3. How sure were you that customers would see the new version, and why?
4. Is there anything you expected to find and didn't?

## Passing the gate

The gate passes when every participant completes tasks 2, 3 and 5 without a hint. Any task failed or needing a hint
becomes an issue (label `P0` if it blocks the task) and is fixed before the session is repeated.

## Facilitator walkthrough (2026-10-20, not a participant)

Before any participant, Claude ran all six tasks in the browser as a first-time manager would, on the containers with
the local models (an "Engraving Service" policy instead of gift wrapping, which already existed in the development
data). It found friction a participant would hit; everything below was fixed and re-checked before the kit was
handed over. This doesn't count towards the gate: the walkthrough's author built the app.

| Task | Result | Found | Fixed |
| --- | --- | --- | --- |
| 1 Sign in | ✓ lands on Documents | — | — |
| 2 Upload and publish | ✓ upload → processing steps → "Review and publish" → "Version 1 of … is live." | — | — |
| 3 Ask as a customer | ✓ "Engraving costs 15 dollars per item." with [1] Engraving Service · p. 1 | — | — |
| 4 Something not covered | ✓ "Not in our documents" with a reason and Contact support | — | — |
| 5 Update the policy | ✗ first try: **Upload document** again with the same title silently created a *second* "Engraving Service"; both would have answered with different prices and v1 would never be archived | **P0** | A new document titled like a live one now asks first (409 `DOCUMENT_TITLE_EXISTS`): "Upload as version 2 of 'Engraving Service'" or "Keep as a separate document" ([m4-01](m4-01-title-clash-offers-new-version.png)). The dialog also says "To update a document, open it and upload a new version." `test_documents_api::test_a_new_document_named_like_a_live_one_asks_first` |
| 5 (cont.) | ✗ the duplicate-file warning inside a *new version* upload offered "Upload anyway as a separate document" | **P0** | It now offers "Upload anyway as version N", and keeps the version choice made after a title clash |
| 5 (retry) | ✓ version 2 published, version 1 Archived ([m4-02](m4-02-version-2-published-v1-archived.png)) | — | — |
| 6 New rule reaches customers | ✓ "Engraving costs $15 per item for non-members and is free for members…", source panel "Version 2 · p. 1" ([m4-03](m4-03-customer-sees-version-2.png)) | — | — |
| (all) | Between 768 and ~1080 px wide, the document table cut off **Updated** and **Actions** (Publish/Retry unreachable) | P1 | The table scrolls sideways inside its card; the page doesn't |
| (setup) | The development data had a "Gift wrapping" document and 40 leftovers | — | `scripts/usability_session.py` runs the session on a clean copy (checked: empty list, gift-wrapping upload, publish and a cited answer, then `down` restored the everyday stack) |

Not fixed (noted): on a clean install the chat's "Try asking" suggestions name documents that don't exist yet
(returns, warranty, E-04), so they answer "Not in our documents". Harmless for the session (the tasks don't use
them); worth making the suggestions come from published documents later (P1).

## Results

| Date | Participant (role, no names) | Tasks completed unaided | Problems found | Fixed in |
| --- | --- | --- | --- | --- |
| *not run yet* | | | | |
