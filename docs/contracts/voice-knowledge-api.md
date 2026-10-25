# Voice knowledge API: `knowledge-answer.v1`

For the Project 2 voice agent ("CallPilot") and anyone building a client for it. Blueprint §9.2, US-07, ADR-001, ADR-017.
The machine-readable contract is [`knowledge-answer.v1.schema.json`](knowledge-answer.v1.schema.json); a test fails if the
API drifts from it.

## Call

```
POST /api/v1/internal/knowledge/answer
Authorization: Bearer <INTERNAL_SERVICE_TOKEN>
Content-Type: application/json
X-Request-ID: voice-request-123          (optional; echoed back and used in TechCare's logs)
```

```json
{
  "request_id": "voice-request-123",
  "audience": "public",
  "channel": "voice",
  "question": "Does the store offer repairs for printers?",
  "conversation_id": "call-abc"
}
```

- `conversation_id` is your call ID. Every question with the same ID joins one conversation.
- `audience` must be `public`. This credential is scoped to TechCare's public documents; `employee` returns **403**.
  Effective rights come from the credential, never from the request (§9.2).

## Response (200)

Header `X-Contract-Version: knowledge-answer.v1`.

```json
{
  "contract_version": "knowledge-answer.v1",
  "request_id": "voice-request-123",
  "answer_status": "answered",
  "grounded": true,
  "answer": "TechCare stores are open on Sunday from 11am to 6pm. [1]",
  "speech_summary": "TechCare stores are open on Sunday from 11am to 6pm.",
  "citations": [{"document_id": "…", "version_id": "…", "page_start": 1}],
  "trace_id": "trace-…"
}
```

| `answer_status` | Meaning | What to say |
| --- | --- | --- |
| `answered` | Grounded in published documents; `citations` lists them | `speech_summary` |
| `insufficient_evidence` | The documents don't cover it; no citations | `speech_summary` ("Our documents don't cover this yet. Our support team can help.") and offer a transfer |
| `direct` | Fixed reply: greeting, "talk to a person", order lookups (not available yet) | `speech_summary` |
| `error` | TechCare's model or index failed; the question is saved | Your fallback line, and offer a transfer |

`speech_summary` is at most two sentences with no citation markers, so it is safe to speak. Never speak `answer` verbatim:
it contains markers like `[1]`.

## Errors

Every error has the same body: `{"error": {"code", "message", "request_id", "details?"}}`.

| Status | Code | Client action |
| --- | --- | --- |
| 400 | `MALFORMED_REQUEST` | Fix the client; don't retry |
| 401 | `UNAUTHENTICATED` | Credential missing or wrong; alert, don't retry |
| 403 | `FORBIDDEN` | Asked for a scope the credential lacks; don't retry |
| 422 | `VALIDATION_ERROR` | `details.fields` says what is wrong; don't retry |
| 5xx / timeout | | Retry once (below), then the fallback |

## Client rules

- **Timeouts:** connect 2 s, total 10 s. TechCare stops a request after 30 s, but a caller shouldn't wait that long.
- **Retries:** at most **one**, and only on a connection error, a timeout, or 502/503/504. Reuse the same `request_id`.
  The call is safe to repeat: it only reads documents, and a repeat adds one more turn to the conversation.
  Never retry 4xx.
- **Fallback when unavailable:** say something like "I can't check our guides right now. Let me connect you with a
  colleague." Never answer from the voice model's own knowledge (ADR-001: the voice agent must work, degraded,
  without this API).
- **Logging:** keep `request_id` and `trace_id` with the call record; TechCare support can find the turn with either.

## Versioning

- `v1` may gain **optional** response fields. Ignore fields you don't know.
- Renaming or removing a field, or changing a meaning, creates `knowledge-answer.v2` on a new route. v1 keeps working
  until Project 2 has moved.
- Check the contract: `cd backend; .venv/Scripts/python -m app.cli.contracts` (`--write` regenerates the schema after
  an intended, reviewed change).
