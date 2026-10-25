# Customer chat: page overrides (§10.1, personas: anonymous and signed-in customer)

Overrides `MASTER.md` only where stated.

## Layout
- Single column, content `max-width: 768px`, centered. Messages measure ≤ 68ch.
- Sticky composer at the bottom (`z-20`), with safe-area padding (`env(safe-area-inset-bottom)`) and the message list padded so the composer never covers the last message.
- Source panel: right drawer ≥1024px (the chat column shifts left; it is not overlaid); bottom sheet at 85dvh below 1024px.
- Density: **low**, with 24px between message groups.

## States (all required)
| State | Design |
| --- | --- |
| Empty (first visit) | h1 "Ask TechCare", one-line disclosure, 4 suggested questions as chips (returns, warranty, printer error, repair booking), "Contact support" link |
| Thinking (< 1.5s) | 3-dot typing indicator in the answer block, labelled "Searching TechCare documents…" for screen readers |
| Streaming (provisional) | `delta` events (ADR-006): text streams in `--fg-muted` under a muted status "Checking sources…". Copy and feedback are disabled. No citations yet |
| Validated | On `final`, the text switches to `--fg`, then the evidence status line and citations appear. If validation failed, cross-fade (200ms) to the Insufficient evidence state. Never show "Answered" before `final` |
| Answered | Status line (success) → answer → citation chips → actions |
| Insufficient evidence | Status line (warning, `SearchX`) → honest message → "Contact support" primary + "Rephrase question" secondary. No citations |
| Needs verification (P1, US-05) | Inline card asking for order number + email/phone; explains why; never reveals order data before verification |
| Error / timeout | Status line (danger) → cause + retry button (rate-limited, shows cooldown) → support link. Composer text preserved |
| Rate-limited | Disabled send with visible countdown text, not just a greyed-out button |

## Components unique to this page
- **Suggested question chip:** outline, full radius, 44px tall, wraps on mobile (no horizontal scroll).
- **Feedback row:** thumbs up/down (icon buttons with labels "Helpful" / "Not helpful"). "Not helpful" expands an optional textarea + reason chips (Wrong, Outdated, Incomplete, Didn't answer).
- **Conversation menu:** "New chat", "Clear history" (confirm dialog), history list for signed-in customers only.

## Accessibility specifics
- Message list is `role="log"` with `aria-live="polite"`. Announce the completed answer once, not every streamed token.
- Enter sends and Shift+Enter adds a newline. The send button stays visible (not keyboard-only).
- Citation chips are buttons; the panel returns focus to the chip on close.
