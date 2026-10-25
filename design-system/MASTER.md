# KnowledgeAI design system: master

> **How to use:** when building a page, read this file and then `design-system/pages/<page>.md`.
> Rules in a page file override this master. Copy and microcopy live in [content-guide.md](content-guide.md).

Curated 2026-10-18 from `ui-ux-pro-max` searches (product, style, color, typography, ux domains, `nextjs` stack).
The auto-generated master was discarded: the generator overwrote it with a "Smart Home/IoT" profile
(OLED dark style, amber CTA, Fira Code headings), which does not fit a customer-facing knowledge product.

## 1. Design direction

| Decision | Choice | Why |
| --- | --- | --- |
| Product type | Knowledge Base + AI/Chatbot Platform | Both top `product` matches for this brief |
| Base style | **Accessible & Ethical + Minimalism** | Knowledge-base recommendation. The users are anonymous public customers, so WCAG AA is the floor |
| Chat style | **AI-Native UI** patterns (streaming text, context cards, sticky composer), *without* the generic "AI purple" | Trust comes from evidence, not novelty. Brand stays navy + blue |
| Trust principle | **Every answer shows its evidence state** | The core promise of US-02/US-04: users can verify claims and see honest "not found" answers |
| Density | Low for customer chat; medium for employee; compact for admin tables | Matches each persona's task (see `docs/product-vision.md` §3) |
| Avoid | Glassmorphism, neumorphism, gradients on text, decorative motion, emoji icons, chat bubbles that hide citations | Low contrast risk, or they compete with evidence |

## 2. Color tokens

Semantic tokens only. Components never use raw hex. All pairs below were verified with a WCAG contrast script.

| Token | Light | Dark | Use | Contrast (on bg / surface) |
| --- | --- | --- | --- | --- |
| `--bg` | `#F8FAFC` | `#0B1220` | Page background | — |
| `--surface` | `#FFFFFF` | `#151E2E` | Cards, panels, answer blocks | — |
| `--surface-muted` | `#F1F5F9` | `#1C2738` | Table header, AI answer block, hover | — |
| `--fg` | `#0F172A` | `#F1F5F9` | Body text | 17.1 / 17.9 · 17.1 / 15.3 |
| `--fg-muted` | `#475569` | `#A3B1C6` | Secondary text, timestamps | 7.2 / 7.6 · 8.6 / 7.7 |
| `--brand` | `#0F172A` | `#F1F5F9` | Logo, primary headings | — |
| `--accent` | `#0369A1` | `#38BDF8` | Primary buttons, links, citation chips, focus | 5.7 / 5.9 · 8.7 / 7.8 |
| `--on-accent` | `#FFFFFF` | `#0B1220` | Text on accent | 5.9 · 8.7 |
| `--user-bubble` | `#E0F2FE` | `#0C3A5B` | User message background | fg 15.6 · 10.8 |
| `--success` | `#047857` | `#34D399` | "Answered from sources", published | 5.2 / 5.5 · 9.7 / 8.7 |
| `--warning` | `#B45309` | `#FBBF24` | "Not in our documents", draft, needs review | 4.8 / 5.0 · 11.2 / 10.0 |
| `--danger` | `#B91C1C` | `#F87171` | Errors, delete, failed ingestion | 6.2 / 6.5 · 6.8 / 6.0 |
| `--border` | `#E2E8F0` | `#273449` | Decorative dividers only | — |
| `--border-strong` | `#64748B` | `#7A8AA3` | Input and checkbox outlines (needs 3:1) | 4.6 / 4.8 · 5.3 / 4.8 |
| `--ring` | `#0369A1` | `#38BDF8` | Focus ring, 2px + 2px offset | — |
| `--scrim` | `rgb(15 23 42 / 0.55)` | `rgb(0 0 0 / 0.6)` | Modal/drawer backdrop | — |

Status colors are **never used alone**: always pair them with an icon and a text label (`color-not-only`).

## 3. Typography

| Role | Font | Weights | Notes |
| --- | --- | --- | --- |
| Headings | **Lexend** | 500, 600 | Designed for reading proficiency. "Corporate Trust" pairing |
| Body / UI | **Source Sans 3** | 400, 600 | Humanist and highly legible; tabular figures for data |
| Code / IDs | **JetBrains Mono** | 400 | Admin diagnostics only: chunk IDs, request IDs |
| Bangla (P1) | **Noto Sans Bengali** | 400, 600 | Fallback in every stack. Lexend and Source Sans don't cover Bengali script |

Load with `next/font/google` (self-hosted, `display: "swap"`, no layout shift). Don't use a CSS `@import`.

| Token | Size / line-height | Use |
| --- | --- | --- |
| `text-xs` | 12 / 16 | Badges, table meta (never body copy) |
| `text-sm` | 14 / 20 | Table cells, helper text, citation labels |
| `text-base` | 16 / 26 | Body, chat messages (min on mobile) |
| `text-lg` | 18 / 28 | Answer lead, card titles |
| `text-xl` | 20 / 28 | Section titles (h3) |
| `text-2xl` | 24 / 32 | Page titles (h2) |
| `text-3xl` | 30 / 36 | Hero / empty-state titles (h1) |

Chat and document text measure: `max-width: 68ch`. Numbers in tables and KPIs: `font-variant-numeric: tabular-nums`.

## 4. Spacing, shape, elevation, layers

- **Spacing:** 4px base. Use 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64. Section rhythm: 24 (in cards), 32 (between groups), 48 (between sections).
- **Radius:** 6 small controls · 8 buttons/inputs · 12 cards/answer blocks · 16 modals/sheets · full for chips and avatars.
- **Elevation:** prefer borders over shadows (minimal style).
  `--shadow-sm: 0 1px 2px rgb(15 23 42 / .06)` for cards ·
  `--shadow-lg: 0 12px 32px rgb(15 23 42 / .16)` for modals, drawers and popovers only.
- **Z-index:** base 0 · sticky composer/header 20 · dropdown/popover 40 · drawer 50 · modal 60 · toast 70.
- **Breakpoints:** 375 (small phone) · 768 (tablet) · 1024 (sidebar appears) · 1440 (max container 1280px).
- **Viewport:** `min-h-dvh`, not `100vh`. No horizontal scroll at 375px. Never disable zoom.

## 5. Motion

- Durations: 150ms (hover/press) · 200ms (popover, chip) · 300ms (drawer, modal enter). Exits are ~70% of the enter duration.
- Easing: `cubic-bezier(0.2, 0, 0, 1)` to enter, `cubic-bezier(0.4, 0, 1, 1)` to exit. Never linear.
- Animate `transform` and `opacity` only. Hover never shifts layout (no `translateY` on cards in lists).
- Meaningful motion only: streaming text, typing indicator, source-panel slide, upload progress.
- `@media (prefers-reduced-motion: reduce)`: disable typing pulse and slides. Streaming text appears in chunks without the caret animation.

## 6. Iconography

- **lucide-react** only, 2px stroke, outline style, sizes 16 / 20 / 24 (`icon-sm/md/lg`).
- Icon-only buttons need an `aria-label` and a hit area of at least 44×44px.
- Fixed meanings: `BadgeCheck` answered · `SearchX` not in documents · `TriangleAlert` error · `LifeBuoy` contact support ·
  `Lock` employee-only · `FileText` source · `ThumbsUp/ThumbsDown` feedback · `RotateCw` retry.

## 7. Core components

| Component | Spec |
| --- | --- |
| **Button** | Height 44 (40 is allowed in dense admin toolbars only). Primary = `--accent` fill, one per view. Secondary = outline `--border-strong`. Destructive = `--danger`, placed apart from the primary button. Loading: disabled + spinner + label kept. |
| **Input / composer** | Height ≥ 44, 16px text (prevents iOS zoom), visible label (the chat composer may use a visually hidden label), helper text below, error below the field with `role="alert"`. |
| **Answer block** | `--surface` card, radius 12. Order: *evidence status line* → answer text (streamed) → numbered citation chips → actions (copy, helpful / not helpful, retry). Never renders citations that the API didn't return. |
| **Evidence status line** | Icon + label + count, e.g. `BadgeCheck` "Answered from 2 sources" (success) · `SearchX` "Not in our documents" (warning) · `TriangleAlert` "Something went wrong" (danger). Announced via `aria-live="polite"` when the stream completes. |
| **Citation chip** | `[1] DemoPrinter-X2 Manual · p. 17–18`. Accent text, outline, full-radius, ≥ 32px tall with 44px hit area. Opens the source panel at the cited page. Keyboard: Enter/Space. |
| **Source panel** | Right drawer (≥1024px, 420px wide) or bottom sheet (<1024px). Shows title, version, page, highlighted quote, "Open document" (only when authorized). Esc closes; focus returns to the chip. |
| **Badge** | Icon + text, `text-xs` semibold, tinted background at 12% of the status color. Used for document status and audience. |
| **Table** | Sticky header, `aria-sort` on sortable columns, row height ≥ 44, tabular numbers, empty and loading (skeleton rows) states, pagination with page size. |
| **Modal / confirm** | Scrim 55%, radius 16, title states the action ("Delete 'Return policy v3'?"), consequence line, buttons named by action. Focus trapped; Esc cancels. |
| **Toast** | Bottom-center on mobile, bottom-right on desktop. `aria-live="polite"`, auto-dismiss after 4s. Undo where reversible. Never steals focus. |
| **Skeleton** | For waits > 300ms. `animate-pulse` is disabled under reduced motion. |

## 8. Navigation

- **Customer chat:** no sidebar. Top bar: TechCare logo, "AI assistant" disclosure badge, "Contact support" link (always visible).
- **Employee and admin:** ≥1024px uses a left sidebar (Assistant, Documents, Quality, Settings) with labels and icons. <1024px uses a top bar + drawer.
  Role badge in the header. Sign out is separated at the bottom of the sidebar.
- Every screen is deep-linkable (`/chat/[conversationId]`, `/admin/documents/[id]`). On route change, focus moves to `<main>`. Provide a skip link.

## 9. Tailwind v4 token block (`frontend/app/globals.css`)

```css
@import "tailwindcss";

@theme {
  --font-heading: var(--font-lexend), var(--font-noto-bengali), ui-sans-serif, system-ui, sans-serif;
  --font-sans: var(--font-source-sans), var(--font-noto-bengali), ui-sans-serif, system-ui, sans-serif;
  --font-mono: var(--font-jetbrains), ui-monospace, monospace;

  --color-bg: #F8FAFC;           --color-surface: #FFFFFF;      --color-surface-muted: #F1F5F9;
  --color-fg: #0F172A;           --color-fg-muted: #475569;
  --color-accent: #0369A1;       --color-on-accent: #FFFFFF;    --color-user-bubble: #E0F2FE;
  --color-success: #047857;      --color-warning: #B45309;      --color-danger: #B91C1C;
  --color-border: #E2E8F0;       --color-border-strong: #64748B; --color-ring: #0369A1;

  --radius-control: 0.5rem;      --radius-card: 0.75rem;        --radius-sheet: 1rem;
  --ease-enter: cubic-bezier(0.2, 0, 0, 1);
  --ease-exit: cubic-bezier(0.4, 0, 1, 1);
}

@layer base {
  :root[data-theme="dark"] {
    --color-bg: #0B1220;         --color-surface: #151E2E;      --color-surface-muted: #1C2738;
    --color-fg: #F1F5F9;         --color-fg-muted: #A3B1C6;
    --color-accent: #38BDF8;     --color-on-accent: #0B1220;    --color-user-bubble: #0C3A5B;
    --color-success: #34D399;    --color-warning: #FBBF24;      --color-danger: #F87171;
    --color-border: #273449;     --color-border-strong: #7A8AA3; --color-ring: #38BDF8;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --color-bg: #0B1220;       --color-surface: #151E2E;      --color-surface-muted: #1C2738;
      --color-fg: #F1F5F9;       --color-fg-muted: #A3B1C6;
      --color-accent: #38BDF8;   --color-on-accent: #0B1220;    --color-user-bubble: #0C3A5B;
      --color-success: #34D399;  --color-warning: #FBBF24;      --color-danger: #F87171;
      --color-border: #273449;   --color-border-strong: #7A8AA3; --color-ring: #38BDF8;
    }
  }
  body { background: var(--color-bg); color: var(--color-fg); }
}
```

## 10. Charts (dataviz method)

Chart marks have their own tokens. The dark-mode text colors in §2 are too light for marks, so they fail the
lightness band. Validated with the `dataviz` skill's `validate_palette.js` (all-pairs) against `--surface` on 2026-10-18.

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--mark-pass` | `#047857` | `#059669` | Gate met, answered |
| `--mark-fail` | `#B91C1C` | `#DC2626` | Gate missed, failed |
| `--mark-series` | `#0369A1` | `#0284C7` | The single series in trend charts |
| `--mark-track` | `#E2E8F0` | `#273449` | Meter track, gridlines (hairline, solid) |
| `--mark-target` | `#0F172A` | `#F1F5F9` | Target marker (2px) |

Validator results: light **passes** CVD (pass↔fail ΔE 8.4 deutan), but series↔pass is 14.0 (normal-vision floor 15 = FAIL).
Dark passes every check. **Rules that follow from this:**
- `--mark-series` and the status marks **never appear in the same chart**. Trend charts are single-series (accent); meters use status colors.
- Pass/fail is **always** icon + text label ("Pass" / "Fail"), never color alone (pass↔fail is only 8.4 apart under deutan).
- A yellower green was tested and rejected: it breaks red/green CVD (ΔE 4.2, then 1.9).
- Every chart has a table-view twin. Values sit as visible text beside each meter; tooltips enhance and never gate.
- Mark specs: lines 2px with round caps, end-dots ≥ 8px with a 2px surface ring, bars ≤ 24px with a 4px rounded data-end.
  Gridlines are 1px solid (never dashed). No dual axes.
- Text never wears a mark color; values and labels use `--fg` / `--fg-muted`.

## 11. Pre-delivery checklist (every screen)

- [ ] Light **and** dark checked separately; text ≥ 4.5:1, input borders and icons ≥ 3:1
- [ ] Status shown with icon + text, never color alone
- [ ] Keyboard: tab order matches visual order, visible focus ring, Esc closes overlays, skip link works
- [ ] Screen reader: landmarks, labelled icon buttons, `aria-live` for streamed answers and errors
- [ ] Touch targets ≥ 44×44 with ≥ 8px gaps; no hover-only actions
- [ ] 375 / 768 / 1024 / 1440 widths, landscape phone, 200% zoom without loss of content
- [ ] `prefers-reduced-motion` honored; no layout shift on load (fonts via `next/font`, reserved image space)
- [ ] Loading, empty, error and no-permission states designed, not just the happy path
- [ ] No emoji icons; lucide only
- [ ] Screenshot saved to `docs/demos/` (blueprint §0)
