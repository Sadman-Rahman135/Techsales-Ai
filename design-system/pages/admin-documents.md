# Admin document center: page overrides (§10.3, persona: support manager / org admin)

## Layout
- App shell with sidebar. Content uses the full container (max 1280px).
- Toolbar: search (debounced 300ms), filters (Audience, Status, Tag), primary **"Upload document"** (the only primary button on the page).
- Density: **compact**. Rows are 44px, `text-sm` cells, tabular numbers.

## Documents table
Columns: Title (+ file type icon) · Owner · Audience badge (`Globe` Public / `Lock` Employee) · Tags · Active version · Status · Updated (relative time, absolute in tooltip and `title`).
- Status badges (icon + text): `CircleDashed` Draft · `Loader` Processing (with %) · `BadgeCheck` Published · `TriangleAlert` Failed · `Archive` Archived.
- Sortable headers with `aria-sort`; the current sort and filters are kept in the URL query string (deep link + back-button restore).
- Row click opens `/admin/documents/[id]`; row actions sit in an overflow menu (`EllipsisVertical`, labelled "Actions for <title>").
- Below 768px the table becomes a card list (title, status badge, audience, updated). The page never scrolls horizontally.
- Empty: "No documents yet. Upload a manual or policy to start answering questions." + Upload button.

## Upload modal → ingestion tracker
- Step 1, file and metadata: drag-and-drop **plus** a "Choose file" button. Accepted types and max size are stated before upload. Fields: title, audience (radio), tags, product model.
- Step 2, progress: stepper `Uploaded → Parsing → Chunking → Embedding → Indexing → Ready`. Each step has an icon and text; failures show the failing step, the cause, and "Retry".
- The modal can be closed during processing; progress continues as a row status and a toast on completion.

## Document detail
- Version timeline (newest first) with a Draft/Published badge. **"Publish version 3"** is an explicit action with a confirmation that states the effect: "New answers will cite version 3. Version 2 will stop appearing."
- Tabs: Preview (original file) · Chunks (diagnostics, mono IDs, page ranges) · Activity (audit log).
- Publication warnings appear inline (warning tone) when the audience changes from Employee → Public.
- **Delete/Archive:** `--danger` button, separated from Publish. The confirmation names the document and the consequence. An audit record is created.
