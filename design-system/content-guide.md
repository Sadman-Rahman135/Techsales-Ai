# KnowledgeAI content guide (UX copy)

Produced with the `design:ux-copy` skill. Brand: **TechCare**, a friendly local electronics retailer and repair center.

## Voice

**Helpful store expert, not a robot and not a salesperson.** Plain words, short sentences, and second person ("you").
The assistant speaks as "TechCare's assistant", never "I am an AI language model". It never guesses, flatters, or apologizes repeatedly.

| Context | Tone | Rule |
| --- | --- | --- |
| Answered | Confident, neutral | Lead with the answer, then the source. No "Great question!" |
| Not in documents | Honest, calm | Say it plainly, then give a next step. Never blame the user |
| Verification | Reassuring | Explain *why* before asking for personal data |
| Error | Empathetic, practical | What happened + what to do. No error codes in the customer UI (show `request_id` under "Details") |
| Admin | Precise, direct | Name objects and consequences exactly |

### Terminology: use the same words everywhere
| Use | Don't use |
| --- | --- |
| source / sources | reference, context, chunk (customer UI) |
| TechCare documents | knowledge base, corpus, database |
| Contact support | escalate, human handoff (customer UI) |
| Published / Draft / Archived | Active / Live / Inactive (mixed) |
| Not in our documents | No results, Unknown, I don't know |

---

## UX copy: AI disclosure banner (customer chat, top bar + empty state)

### Recommended copy
**Badge:** AI assistant
**Empty-state line:** TechCare's AI assistant answers from our published guides and policies, and shows you the source. It can make mistakes. For orders, repairs or anything urgent, **contact support**.

### Alternatives
| Option | Copy | Tone | Best for |
| --- | --- | --- | --- |
| A | Answers come from TechCare's guides and policies. Check the source before you decide. | Brief, neutral | Small screens, the line under the composer |
| B | You're chatting with an automated assistant. A person is available through Contact support. | Formal | Regions with strict AI-disclosure rules |
| C | Ask about products, returns, warranties and repairs. Every answer links to where it came from. | Inviting | Marketing-adjacent landing |

### Rationale
It discloses AI use, sets the scope (published documents), sets a verification expectation, and offers the human route in the same view (§10.1).

---

## UX copy: insufficient evidence (US-04)

### Recommended copy
**Status line:** Not in our documents
**Message:** I couldn't find this in TechCare's published guides or policies, so I won't guess. You can rephrase your question or ask our support team.
**Buttons:** Contact support (primary) · Rephrase question (secondary)

### Alternatives
| Option | Copy | Tone | Best for |
| --- | --- | --- | --- |
| A | Our documents don't cover this yet. Our support team can help. | Shortest | Voice `speech_summary`, mobile |
| B | I found related pages, but none answer this directly. Here's what they cover: … | Transparent | When retrieval found low-relevance hits (show them as "Related", not as citations) |
| C | This needs a person to check. Contact support and we'll look into it. | Directive | Warranty-eligibility or account-specific questions (§2.4 non-goal) |

### Rationale
"So I won't guess" tells the user the refusal protects them. The message gives two next steps, so it never ends in a dead end (`error-recovery`).

---

## UX copy: order-status verification (US-05, P1)

### Recommended copy
**Title:** Confirm it's your order
**Body:** To protect your details, we need the order number and the email or phone number used at checkout.
**Fields:** Order number (helper: "Starts with TC-, on your receipt or confirmation email") · Email or phone
**Button:** Check order status
**Failure:** We couldn't match those details. Check the order number on your receipt and try again, or contact support.

### Rationale
It explains why before asking. The failure message does not reveal whether the order exists (no account enumeration).

---

## UX copy: progress states (SSE `status` events, docs/architecture.md §4)

| Stage | Visible copy | Screen-reader announcement |
| --- | --- | --- |
| `searching` | Searching TechCare documents… | (same; announced once) |
| `rewriting` | Searching TechCare documents… *(unchanged: rewrites are internal)* | — |
| `checking` | Checking sources… | — (announce only the final result) |
| `final` answered | Answered from 2 sources | "Answer ready, from 2 sources" |

---

## UX copy: citations

| Element | Copy |
| --- | --- |
| Status line | Answered from 2 sources (singular: "Answered from 1 source") |
| Chip | [1] DemoPrinter-X2 User Manual · p. 17–18 |
| Chip, internal | [2] Printer service procedure · Employee only |
| Panel header | Source 1 of 2 |
| Panel action | Open document (hidden when the user isn't authorized) |
| Outdated guard (admin preview) | This citation points to version 2. Version 3 is published. |

---

## UX copy: escalation CTA

**Link (always visible):** Contact support
**After "Not helpful":** Sorry this didn't help. Want to tell us what was missing? (optional textarea) · **Send feedback** · or **Contact support**
**Employee ticket (P1):** Create ticket · Review the summary before sending. Customers see only what you send.

---

## UX copy: provider / system errors

| Case | Copy | Actions |
| --- | --- | --- |
| LLM/provider failure | Something went wrong while answering. Your question is saved, so try again in a moment. | Try again · Contact support |
| Timeout | This is taking longer than usual. | Keep waiting · Try again |
| Rate limited | You've asked a lot of questions quickly. You can ask again in 30 seconds. | (countdown) |
| Offline | You're offline. We'll send your question when you reconnect. | — |
| Upload failed (admin) | We couldn't read this PDF. It may be a scanned image, and we can't read scanned PDFs yet. Upload a text-based PDF instead. | Retry · Choose another file |
| Publish confirm (admin) | **Publish version 3 of "Return policy"?** New answers will cite version 3. Version 2 will stop appearing in answers. | Publish version 3 · Keep version 2 |
| Delete confirm (admin) | **Delete "Return policy"?** It stops appearing in answers immediately. The deletion is logged. | Delete document · Cancel |

"Details" disclosure (customer error): `Reference: req-…`. Support can use it to find the request without the customer sharing their conversation.

---

## Localization notes
- Plan for **Bangla (P1)**: allow +30–40% text expansion; avoid fixed-width buttons; Bengali script needs larger line-height (1.7).
- Avoid idioms ("hang tight", "no worries"). Keep sentences under 20 words.
- Page references: use `p. 17–18` in English; localize the abbreviation, don't concatenate strings (`t("citation.pages", {start, end})`).
- Pluralization goes through ICU message format ("1 source" / "2 sources").
- Dates are relative in the UI ("2 hours ago") with an absolute tooltip, in the tenant timezone (§5.1).
