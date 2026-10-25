# Employee assistant: page overrides (§10.2, persona: store employee)

## Layout
- App shell: sidebar ≥1024px; top bar + drawer below that. Chat column `max-width: 840px`.
- **Filter bar** above the conversation: Product model (combobox with search), Brand, Category, Document type.
  Selected filters show as removable chips; "Clear filters" sits at the end. On mobile the filters collapse behind a "Filters (2)" button that opens a bottom sheet.
- Density: **medium**, with 16px between message groups. Employees scan quickly, often at a counter with a customer waiting.

## Role and audience signals
- Header shows a role badge: `BadgeCheck` "Employee · TechCare Downtown".
- Citations from internal documents show a `Lock` badge "Employee only" next to the chip, so staff know not to read them verbatim to customers.
- An answer mixing public and internal sources lists public sources first.

## Components unique to this page
- **Copy for customer** action (secondary): copies only the public-source sentences plus citations, for pasting into a customer reply.
- **Ticket handoff (P1):** "Create ticket" opens a side panel with an auto-drafted case summary the employee must review before submitting.

## States
- Same as customer chat, plus **"No results with these filters"**: suggest removing the most restrictive filter, with a one-click action.
