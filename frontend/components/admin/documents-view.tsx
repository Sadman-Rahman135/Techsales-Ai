"use client";

import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Download,
  FileText,
  LoaderCircle,
  RotateCw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";

import { AudienceBadge, StatusBadge } from "@/components/admin/badges";
import { buttonClass, Modal } from "@/components/admin/modal";
import { ToastProvider, useToast } from "@/components/admin/toast";
import { type ReadyVersion, UploadDialog } from "@/components/admin/upload-dialog";
import { When } from "@/components/admin/when";
import { type ApiError, type DocumentPage, type DocumentSummary, type Facets, type Job, METADATA_FILTERS } from "@/lib/documents";

export type ListQuery = {
  q: string;
  audience: string;
  tag: string;
  sort: string;
  offset: number;
  // Metadata search (§2.2 P1): under "More filters".
  brand: string;
  category: string;
  department: string;
  language: string;
  from: string; // effective date range, YYYY-MM-DD
  to: string;
};

const NO_FILTERS = { q: "", audience: "", tag: "", brand: "", category: "", department: "", language: "", from: "", to: "" };

const REFRESH_MS = 2000;

type ViewProps = {
  page: DocumentPage;
  query: ListQuery;
  tags: string[];
  facets: Facets;
  isAdmin: boolean;
  timezone: string;
  renderedAt: number;
};

export function DocumentsView(props: ViewProps) {
  return (
    <ToastProvider>
      <DocumentsInner {...props} />
    </ToastProvider>
  );
}

type Confirm =
  | { kind: "publish"; version: ReadyVersion; activeVersion: number | null; audience: string }
  | { kind: "delete"; document: DocumentSummary };

function DocumentsInner({ page, query, tags, facets, isAdmin, timezone, renderedAt }: ViewProps) {
  const router = useRouter();
  const pathname = usePathname();
  const toast = useToast();
  const [uploadOpen, setUploadOpen] = useState(false);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [, startTransition] = useTransition();
  const refresh = () => startTransition(() => router.refresh());

  // While anything is processing, re-fetch the list every 2 s and announce documents that finished.
  const processing = page.items.some((d) => d.status === "processing");
  const previous = useRef(new Map(page.items.map((d) => [d.id, d.status])));
  useEffect(() => {
    for (const doc of page.items) {
      const before = previous.current.get(doc.id);
      if (before === "processing" && doc.status === "ready") toast("success", `"${doc.title}" is ready to publish.`);
      if (before === "processing" && doc.status === "failed") toast("danger", `"${doc.title}" couldn't be processed.`);
    }
    previous.current = new Map(page.items.map((d) => [d.id, d.status]));
  }, [page.items, toast]);
  useEffect(() => {
    if (!processing) return;
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh is stable enough; re-arm only on state change
  }, [processing]);

  function navigate(next: Partial<ListQuery>) {
    const merged = { ...query, offset: 0, ...next };
    const params = new URLSearchParams();
    if (merged.q) params.set("q", merged.q);
    if (merged.audience) params.set("audience", merged.audience);
    if (merged.tag) params.set("tag", merged.tag);
    for (const key of METADATA_FILTERS) if (merged[key]) params.set(key, merged[key]);
    if (merged.sort && merged.sort !== "-updated") params.set("sort", merged.sort);
    if (merged.offset) params.set("offset", String(merged.offset));
    const search = params.toString();
    startTransition(() => router.replace(search ? `${pathname}?${search}` : pathname, { scroll: false }));
  }

  const filtered = Boolean(query.q || query.audience || query.tag || METADATA_FILTERS.some((k) => query[k]));
  const empty = page.total === 0;

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold">Documents</h1>
          <p className="mt-1 text-sm text-fg-muted">
            Manuals and policies the assistant answers from. Only published versions answer questions.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <a href="/api/admin/exports/documents" download className={buttonClass.secondary}>
            <Download className="size-4" aria-hidden="true" />
            Export CSV
          </a>
          <button type="button" onClick={() => setUploadOpen(true)} className={buttonClass.primary}>
            <Upload className="size-4" aria-hidden="true" />
            Upload document
          </button>
        </div>
      </div>

      {empty && !filtered ? (
        <EmptyState onUpload={() => setUploadOpen(true)} />
      ) : (
        <>
          <Toolbar query={query} tags={tags} facets={facets} onChange={navigate} />
          {empty ? (
            <p className="mt-6 rounded-card border border-border bg-surface p-6 text-center text-fg-muted">
              No documents match these filters.{" "}
              <button
                type="button"
                className="font-semibold text-accent underline-offset-2 hover:underline"
                onClick={() => navigate(NO_FILTERS)}
              >
                Clear filters
              </button>
            </p>
          ) : (
            <>
              <DocumentTable
                items={page.items}
                sort={query.sort}
                timezone={timezone}
                now={renderedAt}
                onSort={(sort) => navigate({ sort })}
                actions={(doc) => <RowActions doc={doc} isAdmin={isAdmin} onConfirm={setConfirm} onChanged={refresh} />}
              />
              <DocumentCards
                items={page.items}
                timezone={timezone}
                now={renderedAt}
                actions={(doc) => <RowActions doc={doc} isAdmin={isAdmin} onConfirm={setConfirm} onChanged={refresh} />}
              />
              <Pagination page={page} onOffset={(offset) => navigate({ offset })} />
            </>
          )}
        </>
      )}

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onChanged={refresh}
        onPublish={(version) => {
          const doc = page.items.find((d) => d.id === version.documentId);
          setConfirm({
            kind: "publish",
            version,
            activeVersion: doc?.active_version_number ?? null,
            audience: doc?.audience ?? "public",
          });
        }}
      />
      <ConfirmDialog
        confirm={confirm}
        onClose={() => setConfirm(null)}
        onDone={(text) => {
          toast("success", text);
          refresh();
        }}
      />
    </>
  );
}

// ---------- toolbar ----------

function Toolbar({
  query,
  tags,
  facets,
  onChange,
}: {
  query: ListQuery;
  tags: string[];
  facets: Facets;
  onChange: (next: Partial<ListQuery>) => void;
}) {
  const [text, setText] = useState(query.q);
  useEffect(() => {
    if (text === query.q) return;
    const timer = setTimeout(() => onChange({ q: text.trim() }), 300); // debounced (page spec: 300 ms)
    return () => clearTimeout(timer);
  }, [text, query.q, onChange]);

  return (
    <div role="search" className="mt-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex-1">
          <label htmlFor="doc-search" className="block text-sm font-semibold">
            Search
          </label>
          <div className="relative mt-1.5">
            <Search
              className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-fg-muted"
              aria-hidden="true"
            />
            <input
              id="doc-search"
              type="search"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Title, product, brand or department"
              className="block min-h-10 w-full rounded-control border border-border-strong bg-surface pr-10 pl-9 text-base text-fg placeholder:text-fg-muted/70"
            />
            {text ? (
              <button
                type="button"
                onClick={() => setText("")}
                aria-label="Clear search"
                className="absolute top-1/2 right-0 grid size-10 -translate-y-1/2 place-items-center text-fg-muted hover:text-fg"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            ) : null}
          </div>
        </div>
        <div>
          <label htmlFor="doc-audience" className="block text-sm font-semibold">
            Audience
          </label>
          <select
            id="doc-audience"
            value={query.audience}
            onChange={(e) => onChange({ audience: e.target.value })}
            className="mt-1.5 block min-h-10 w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg sm:w-44"
          >
            <option value="">All audiences</option>
            <option value="public">Public</option>
            <option value="employee">Employees only</option>
          </select>
        </div>
        {tags.length ? (
          <div>
            <label htmlFor="doc-tag" className="block text-sm font-semibold">
              Tag
            </label>
            <select
              id="doc-tag"
              value={query.tag}
              onChange={(e) => onChange({ tag: e.target.value })}
              className="mt-1.5 block min-h-10 w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg sm:w-44"
            >
              <option value="">All tags</option>
              {tags.map((tag) => (
                <option key={tag} value={tag}>
                  {tag}
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </div>
      <MoreFilters query={query} facets={facets} onChange={onChange} />
    </div>
  );
}

const LANGUAGES: Record<string, string> = { en: "English", bn: "Bangla" };

function MoreFilters({
  query,
  facets,
  onChange,
}: {
  query: ListQuery;
  facets: Facets;
  onChange: (next: Partial<ListQuery>) => void;
}) {
  const active = METADATA_FILTERS.filter((k) => query[k]).length;
  const selects = [
    { key: "brand", label: "Brand", all: "All brands", values: facets.brands ?? [] },
    { key: "category", label: "Category", all: "All categories", values: facets.categories ?? [] },
    { key: "department", label: "Department", all: "All departments", values: facets.departments ?? [] },
    { key: "language", label: "Language", all: "All languages", values: facets.languages ?? [] },
  ] as const;
  return (
    <details className="mt-2" open={active > 0}>
      <summary className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 text-sm font-semibold text-accent">
        More filters{active ? ` (${active})` : ""}
      </summary>
      <div className="mt-2 grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {selects.map(({ key, label, all, values }) => (
          <div key={key}>
            <label htmlFor={`doc-${key}`} className="block text-sm font-semibold">
              {label}
            </label>
            <select
              id={`doc-${key}`}
              value={query[key]}
              onChange={(e) => onChange({ [key]: e.target.value })}
              className="mt-1.5 block min-h-10 w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg"
            >
              <option value="">{all}</option>
              {values.map((v) => (
                <option key={v} value={v}>
                  {key === "language" ? (LANGUAGES[v] ?? v) : v}
                </option>
              ))}
            </select>
          </div>
        ))}
        {(["from", "to"] as const).map((key) => (
          <div key={key}>
            <label htmlFor={`doc-${key}`} className="block text-sm font-semibold">
              {key === "from" ? "Effective from" : "Effective until"}
            </label>
            <input
              id={`doc-${key}`}
              type="date"
              value={query[key]}
              onChange={(e) => onChange({ [key]: e.target.value })}
              className="mt-1.5 block min-h-10 w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg"
            />
          </div>
        ))}
      </div>
    </details>
  );
}

// ---------- table (≥768px) and cards (<768px) ----------

type Columns = {
  items: DocumentSummary[];
  timezone: string;
  now: number;
  actions: (doc: DocumentSummary) => React.ReactNode;
};

function DocumentTable({
  items,
  sort,
  timezone,
  now,
  onSort,
  actions,
}: Columns & { sort: string; onSort: (sort: string) => void }) {
  return (
    // Scrolls sideways between the card breakpoint and ~1080px instead of clipping Updated and Actions.
    <div className="mt-4 hidden overflow-x-auto rounded-card border border-border bg-surface md:block">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Documents, sorted by {sort.replace("-", "")}</caption>
        <thead className="sticky top-0 bg-surface-muted text-xs text-fg-muted">
          <tr>
            <SortHeader label="Title" field="title" sort={sort} onSort={onSort} />
            <th scope="col" className="px-4 py-2 font-semibold">
              Owner
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              Audience
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              Tags
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              Active version
            </th>
            <th scope="col" className="px-4 py-2 font-semibold">
              Status
            </th>
            <SortHeader label="Updated" field="updated" sort={sort} onSort={onSort} />
            <th scope="col" className="px-4 py-2 font-semibold">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {items.map((doc) => (
            <tr key={doc.id} className="h-11">
              <td className="max-w-80 px-4 py-2">
                <div className="flex items-center gap-2">
                  <FileText className="size-4 shrink-0 text-fg-muted" aria-hidden="true" />
                  <div className="min-w-0">
                    <Link
                      href={`/admin/documents/${doc.id}`}
                      className="block truncate font-semibold text-fg underline-offset-2 hover:text-accent hover:underline"
                    >
                      {doc.title}
                    </Link>
                    <p className="truncate text-xs text-fg-muted">
                      {[doc.product_model, doc.latest_version?.original_filename].filter(Boolean).join(" · ")}
                    </p>
                  </div>
                </div>
              </td>
              <td className="max-w-36 truncate px-4 py-2 text-fg-muted">{doc.owner ?? "Unknown"}</td>
              <td className="px-4 py-2">
                <AudienceBadge audience={doc.audience} />
              </td>
              <td className="px-4 py-2">
                <Tags tags={doc.tags} />
              </td>
              <td className="px-4 py-2 tabular-nums">
                {doc.active_version_number ? (
                  `v${doc.active_version_number}`
                ) : (
                  <span className="text-fg-muted">None</span>
                )}
              </td>
              <td className="px-4 py-2">
                <StatusCell doc={doc} />
              </td>
              <td className="px-4 py-2 whitespace-nowrap text-fg-muted">
                <When iso={doc.updated_at} timezone={timezone} now={now} />
              </td>
              <td className="px-4 py-2 text-right">{actions(doc)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SortHeader({
  label,
  field,
  sort,
  onSort,
}: {
  label: string;
  field: string;
  sort: string;
  onSort: (s: string) => void;
}) {
  const active = sort.replace("-", "") === field;
  const descending = sort.startsWith("-");
  const Icon = !active ? ArrowUpDown : descending ? ArrowDown : ArrowUp;
  return (
    <th
      scope="col"
      aria-sort={active ? (descending ? "descending" : "ascending") : "none"}
      className="px-2 py-1 font-semibold"
    >
      <button
        type="button"
        onClick={() => onSort(active && !descending ? `-${field}` : field)}
        className="inline-flex min-h-10 items-center gap-1 rounded-control px-2 hover:text-fg"
      >
        {label}
        <Icon className="size-3.5" aria-hidden="true" />
      </button>
    </th>
  );
}

function DocumentCards({ items, timezone, now, actions }: Columns) {
  return (
    <ul className="mt-4 space-y-3 md:hidden">
      {items.map((doc) => (
        <li key={doc.id} className="rounded-card border border-border bg-surface p-4">
          <div className="flex items-start justify-between gap-3">
            <Link
              href={`/admin/documents/${doc.id}`}
              className="min-w-0 font-semibold break-words text-fg underline-offset-2 hover:text-accent hover:underline"
            >
              {doc.title}
            </Link>
            <StatusCell doc={doc} />
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
            <AudienceBadge audience={doc.audience} />
            <span>{doc.active_version_number ? `Live: v${doc.active_version_number}` : "Not live"}</span>
            <span aria-hidden="true">·</span>
            <When iso={doc.updated_at} timezone={timezone} now={now} />
          </div>
          {doc.tags.length ? (
            <div className="mt-2">
              <Tags tags={doc.tags} />
            </div>
          ) : null}
          <div className="mt-3 flex flex-wrap gap-2 has-[>span:empty]:hidden">{actions(doc)}</div>
        </li>
      ))}
    </ul>
  );
}

function StatusCell({ doc }: { doc: DocumentSummary }) {
  const job = doc.latest_version?.job;
  return (
    <span className="inline-flex flex-col items-start gap-0.5">
      <StatusBadge status={doc.status} />
      {doc.status === "processing" && job ? (
        <span className="text-xs text-fg-muted capitalize">{job.state === "queued" ? "Waiting" : job.state}…</span>
      ) : null}
      {doc.status === "failed" && job?.error_message ? (
        <span className="max-w-56 text-xs text-fg-muted">{job.error_message}</span>
      ) : null}
    </span>
  );
}

function Tags({ tags }: { tags: string[] }) {
  if (!tags.length) return <span className="text-fg-muted">None</span>;
  return (
    <ul className="flex flex-wrap gap-1" aria-label="Tags">
      {tags.map((tag) => (
        <li key={tag} className="rounded-full bg-surface-muted px-2 py-0.5 text-xs text-fg-muted">
          {tag}
        </li>
      ))}
    </ul>
  );
}

// ---------- row actions ----------

function RowActions({
  doc,
  isAdmin,
  onConfirm,
  onChanged,
}: {
  doc: DocumentSummary;
  isAdmin: boolean;
  onConfirm: (c: Confirm) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const version = doc.latest_version;
  const job: Job | null = version?.job ?? null;

  async function jobAction(action: "retry" | "cancel") {
    if (!job) return;
    setBusy(true);
    const response = await fetch(`/api/admin/ingestion-jobs/${job.id}/${action}`, { method: "POST" }).catch(() => null);
    setBusy(false);
    if (!response?.ok) {
      const error = response
        ? ((await response.json()) as { error: ApiError }).error.message
        : "That didn't reach us. Try again.";
      toast("danger", error);
      return;
    }
    toast("success", action === "retry" ? `Retrying "${doc.title}".` : `Stopping "${doc.title}".`);
    onChanged();
  }

  return (
    <span className="inline-flex flex-wrap justify-end gap-1">
      {doc.status === "ready" && version ? (
        <button
          type="button"
          className={buttonClass.quiet}
          aria-label={`Publish version ${version.version_number} of ${doc.title}`}
          onClick={() =>
            onConfirm({
              kind: "publish",
              version: {
                documentId: doc.id,
                versionId: version.id,
                versionNumber: version.version_number,
                title: doc.title,
              },
              activeVersion: doc.active_version_number,
              audience: doc.audience,
            })
          }
        >
          Publish v{version.version_number}
        </button>
      ) : null}
      {(doc.status === "failed" || doc.status === "cancelled") && job ? (
        <button
          type="button"
          disabled={busy}
          className={buttonClass.quiet}
          onClick={() => jobAction("retry")}
          aria-label={`Retry processing ${doc.title}`}
        >
          {busy ? (
            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <RotateCw className="size-4" aria-hidden="true" />
          )}
          Retry
        </button>
      ) : null}
      {doc.status === "processing" && job && !job.cancel_requested ? (
        <button
          type="button"
          disabled={busy}
          className={buttonClass.quiet}
          onClick={() => jobAction("cancel")}
          aria-label={`Cancel processing ${doc.title}`}
        >
          Cancel
        </button>
      ) : null}
      {isAdmin ? (
        <button
          type="button"
          onClick={() => onConfirm({ kind: "delete", document: doc })}
          aria-label={`Delete ${doc.title}`}
          className="inline-flex min-h-10 items-center gap-1.5 rounded-control px-3 text-sm font-semibold text-danger transition-colors duration-150 hover:bg-danger/10"
        >
          <Trash2 className="size-4" aria-hidden="true" />
          <span className="md:sr-only">Delete</span>
        </button>
      ) : null}
    </span>
  );
}

// ---------- confirmations (copy: design-system/content-guide.md) ----------

function ConfirmDialog({
  confirm,
  onClose,
  onDone,
}: {
  confirm: Confirm | null;
  onClose: () => void;
  onDone: (text: string) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const shown = confirm;

  function close() {
    setError(null);
    onClose();
  }

  async function run() {
    if (!shown) return;
    setBusy(true);
    setError(null);
    const request =
      shown.kind === "publish"
        ? fetch(`/api/admin/documents/${shown.version.documentId}/publish`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ version_id: shown.version.versionId }),
          })
        : fetch(`/api/admin/documents/${shown.document.id}`, { method: "DELETE" });
    const response = await request.catch(() => null);
    setBusy(false);
    if (!response?.ok) {
      setError(
        response ? ((await response.json()) as { error: ApiError }).error.message : "That didn't reach us. Try again.",
      );
      return;
    }
    close();
    onDone(
      shown.kind === "publish"
        ? `Version ${shown.version.versionNumber} of "${shown.version.title}" is live.`
        : `"${shown.document.title}" was deleted.`,
    );
  }

  // Same element type in both branches, so the <dialog> instance is reused and closes cleanly.
  if (!shown)
    return (
      <Modal open={false} onClose={close} title="">
        {null}
      </Modal>
    );

  const publish = shown.kind === "publish";
  const title = publish
    ? `Publish version ${shown.version.versionNumber} of "${shown.version.title}"?`
    : `Delete "${shown.document.title}"?`;
  const consequence = publish
    ? shown.activeVersion
      ? `New answers will cite version ${shown.version.versionNumber}. Version ${shown.activeVersion} will stop appearing in answers.`
      : `New answers can cite it. ${shown.audience === "employee" ? "Only employees will see it." : "Customers and employees will see it."}`
    : "It stops appearing in answers immediately. The deletion is logged.";

  return (
    <Modal open={confirm !== null} onClose={close} title={title}>
      <p className="text-sm">{consequence}</p>
      {error ? (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      ) : null}
      <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={close} className={buttonClass.secondary}>
          {publish ? (shown.activeVersion ? `Keep version ${shown.activeVersion}` : "Not yet") : "Cancel"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={run}
          className={publish ? buttonClass.primary : buttonClass.danger}
        >
          {busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : null}
          {publish ? `Publish version ${shown.version.versionNumber}` : "Delete document"}
        </button>
      </div>
    </Modal>
  );
}

// ---------- empty state and paging ----------

function EmptyState({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="mt-8 flex flex-col items-center rounded-card border border-dashed border-border-strong bg-surface px-6 py-14 text-center">
      <span className="grid size-12 place-items-center rounded-full bg-surface-muted">
        <FileText className="size-6 text-fg-muted" aria-hidden="true" />
      </span>
      <p className="mt-4 max-w-[46ch] text-fg">
        No documents yet. Upload a manual or policy to start answering questions.
      </p>
      <button type="button" onClick={onUpload} className={`${buttonClass.secondary} mt-5`}>
        <Upload className="size-4" aria-hidden="true" />
        Upload document
      </button>
    </div>
  );
}

function Pagination({ page, onOffset }: { page: DocumentPage; onOffset: (offset: number) => void }) {
  if (page.total <= page.limit) {
    return (
      <p className="mt-3 text-sm text-fg-muted">
        {page.total} {page.total === 1 ? "document" : "documents"}
      </p>
    );
  }
  const from = page.offset + 1;
  const to = Math.min(page.offset + page.limit, page.total);
  return (
    <nav aria-label="Pages" className="mt-3 flex items-center justify-between gap-3 text-sm">
      <p className="text-fg-muted tabular-nums">
        {from}–{to} of {page.total}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          disabled={page.offset === 0}
          onClick={() => onOffset(Math.max(0, page.offset - page.limit))}
          className={buttonClass.secondary}
        >
          Previous
        </button>
        <button
          type="button"
          disabled={to >= page.total}
          onClick={() => onOffset(page.offset + page.limit)}
          className={buttonClass.secondary}
        >
          Next
        </button>
      </div>
    </nav>
  );
}
