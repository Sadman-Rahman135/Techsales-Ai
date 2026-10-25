"use client";

import {
  BadgeCheck,
  Download,
  ExternalLink,
  Pencil,
  Globe,
  LoaderCircle,
  Lock,
  RotateCw,
  TriangleAlert,
  Trash2,
  Upload,
  XCircle,
} from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition, type KeyboardEvent } from "react";

import { AudienceBadge, StatusBadge } from "@/components/admin/badges";
import { buttonClass, Modal } from "@/components/admin/modal";
import { ToastProvider, useToast } from "@/components/admin/toast";
import { describeWarning, UploadDialog } from "@/components/admin/upload-dialog";
import { absoluteTime, When } from "@/components/admin/when";
import {
  type ActivityEvent,
  type ApiError,
  type ChunkReport,
  type DocumentDetail,
  type DocumentStatus,
  formatBytes,
  type Preview,
  type Version,
} from "@/lib/documents";

type Tab = "preview" | "chunks" | "activity";

type Props = {
  document: DocumentDetail;
  activity: ActivityEvent[];
  isAdmin: boolean;
  timezone: string;
  renderedAt: number;
  initialTab: Tab;
  initialVersion: string | null;
};

const REFRESH_MS = 2000;

const chunks = (n: number) => (n === 1 ? "1 chunk" : `${n} chunks`);

export function DocumentDetailView(props: Props) {
  return (
    <ToastProvider>
      <Detail {...props} />
    </ToastProvider>
  );
}

function versionStatus(document: DocumentDetail, version: Version): DocumentStatus {
  const job = version.job;
  if (job && !["done", "failed", "cancelled"].includes(job.state)) return "processing";
  if (version.status === "published") return "published";
  if (version.status === "ready") return "ready";
  if (version.status === "failed") return job?.state === "cancelled" ? "cancelled" : "failed";
  if (version.status === "archived") return "archived";
  return "draft";
}

function Detail({ document, activity, isAdmin, timezone, renderedAt, initialTab, initialVersion }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const toast = useToast();
  const [, startTransition] = useTransition();
  const refresh = () => startTransition(() => router.refresh());
  const versions = document.versions;
  const active = versions.find((v) => v.version_number === document.active_version_number) ?? null;
  const [tab, setTab] = useState<Tab>(initialTab);
  const [selected, setSelected] = useState<string | null>(
    initialVersion && versions.some((v) => v.id === initialVersion) ? initialVersion : (active ?? versions[0])?.id ?? null,
  );
  const [uploadOpen, setUploadOpen] = useState(false);
  const [publishing, setPublishing] = useState<Version | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [editing, setEditing] = useState(false);
  const selectedVersion = versions.find((v) => v.id === selected) ?? null;

  const processing = versions.some((v) => versionStatus(document, v) === "processing");
  useEffect(() => {
    if (!processing) return;
    const timer = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-arm only when processing starts or stops
  }, [processing]);

  // Tab and version live in the URL, so a view can be shared and the back button restores it.
  function show(nextTab: Tab, version = selected) {
    setTab(nextTab);
    setSelected(version);
    const params = new URLSearchParams({ tab: nextTab });
    if (version) params.set("version", version);
    window.history.replaceState(null, "", `${pathname}?${params}`);
  }

  async function jobAction(version: Version, action: "retry" | "cancel") {
    if (!version.job) return;
    const response = await fetch(`/api/admin/ingestion-jobs/${version.job.id}/${action}`, { method: "POST" }).catch(
      () => null,
    );
    if (!response?.ok) {
      toast("danger", response ? ((await response.json()) as { error: ApiError }).error.message : "That didn't reach us.");
      return;
    }
    toast("success", action === "retry" ? `Retrying version ${version.version_number}.` : `Stopping version ${version.version_number}.`);
    refresh();
  }

  return (
    <>
      <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-heading text-2xl font-semibold break-words">{document.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-fg-muted">
            <StatusBadge status={document.status} />
            <AudienceBadge audience={document.audience} />
            {document.product_model ? <span>{document.product_model}</span> : null}
            {document.category ? <span>· {document.category}</span> : null}
            {document.brand ? <span>· {document.brand}</span> : null}
            {document.department ? <span>· {document.department}</span> : null}
            {document.effective_date ? (
              <span>
                · Effective <time dateTime={document.effective_date}>{document.effective_date}</time>
              </span>
            ) : null}
            <span>· Owner: {document.owner ?? "Unknown"}</span>
          </div>
          {document.tags.length ? (
            <ul className="mt-2 flex flex-wrap gap-1" aria-label="Tags">
              {document.tags.map((tag) => (
                <li key={tag} className="rounded-full bg-surface-muted px-2 py-0.5 text-xs text-fg-muted">
                  {tag}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => setEditing(true)} className={buttonClass.secondary}>
            <Pencil className="size-4" aria-hidden="true" />
            Edit details
          </button>
          <button type="button" onClick={() => setUploadOpen(true)} className={buttonClass.primary}>
            <Upload className="size-4" aria-hidden="true" />
            Upload new version
          </button>
          {isAdmin ? (
            <button
              type="button"
              onClick={() => setDeleting(true)}
              className="inline-flex min-h-11 items-center gap-2 rounded-control border border-danger/50 px-4 font-semibold text-danger hover:bg-danger/10 sm:ml-4"
            >
              <Trash2 className="size-4" aria-hidden="true" />
              Delete
            </button>
          ) : null}
        </div>
      </div>

      {editing ? (
        <DetailsDialog
          document={document}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            toast("success", "Details saved.");
            refresh();
          }}
        />
      ) : null}

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(300px,380px)_1fr]">
        <section aria-labelledby="versions-title" className="min-w-0">
          <h2 id="versions-title" className="font-heading text-lg font-semibold">
            Versions
          </h2>
          <ol className="mt-3 space-y-3">
            {versions.map((version) => {
              const status = versionStatus(document, version);
              const isSelected = version.id === selected;
              return (
                <li
                  key={version.id}
                  className={`rounded-card border bg-surface p-4 ${isSelected ? "border-accent" : "border-border"}`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-semibold">Version {version.version_number}</p>
                    <StatusBadge status={status} />
                  </div>
                  <p className="mt-1 truncate text-sm text-fg-muted" title={version.original_filename}>
                    {version.original_filename} · {formatBytes(version.size_bytes)}
                  </p>
                  <p className="mt-1 text-sm text-fg-muted">
                    Uploaded <When iso={version.created_at} timezone={timezone} now={renderedAt} />
                    {version.published_at ? (
                      <>
                        {" "}· published <When iso={version.published_at} timezone={timezone} now={renderedAt} />
                      </>
                    ) : null}
                  </p>
                  {version.expected_chunk_count !== null ? (
                    <p className="mt-1 text-sm text-fg-muted tabular-nums">
                      {version.indexed_chunk_count} of {chunks(version.expected_chunk_count)} indexed
                    </p>
                  ) : null}
                  {status === "failed" && version.job?.error_message ? (
                    <p className="mt-2 text-sm">
                      <TriangleAlert className="mr-1 inline size-4 text-danger" aria-hidden="true" />
                      {version.job.error_message}
                    </p>
                  ) : null}
                  <div className="mt-3 flex flex-wrap gap-1">
                    {status === "ready" ? (
                      <button type="button" onClick={() => setPublishing(version)} className={buttonClass.quiet}>
                        <BadgeCheck className="size-4" aria-hidden="true" />
                        Publish version {version.version_number}
                      </button>
                    ) : null}
                    {(status === "failed" || status === "cancelled") && version.job ? (
                      <button type="button" onClick={() => jobAction(version, "retry")} className={buttonClass.quiet}>
                        <RotateCw className="size-4" aria-hidden="true" />
                        Retry
                      </button>
                    ) : null}
                    {status === "processing" && version.job && !version.job.cancel_requested ? (
                      <button type="button" onClick={() => jobAction(version, "cancel")} className={buttonClass.quiet}>
                        <XCircle className="size-4" aria-hidden="true" />
                        Cancel
                      </button>
                    ) : null}
                    <button
                      type="button"
                      aria-pressed={isSelected}
                      onClick={() => show(tab === "activity" ? "preview" : tab, version.id)}
                      className={buttonClass.quiet}
                    >
                      {isSelected ? "Showing below" : "Inspect"}
                    </button>
                  </div>
                </li>
              );
            })}
          </ol>
        </section>

        <section aria-label="Inspect" className="min-w-0">
          <Tabs tab={tab} onChange={(t) => show(t)} />
          <div
            role="tabpanel"
            id={`panel-${tab}`}
            aria-labelledby={`tab-${tab}`}
            tabIndex={0}
            className="mt-3 rounded-card border border-border bg-surface p-4"
          >
            {tab === "activity" ? (
              <ActivityList events={activity} timezone={timezone} />
            ) : !selectedVersion ? (
              <p className="text-fg-muted">No versions yet.</p>
            ) : tab === "preview" ? (
              <PreviewPane document={document} version={selectedVersion} />
            ) : (
              <ChunksPane documentId={document.id} version={selectedVersion} />
            )}
          </div>
        </section>
      </div>

      <UploadDialog
        open={uploadOpen}
        target={{
          documentId: document.id,
          title: document.title,
          nextVersion: Math.max(0, ...versions.map((v) => v.version_number)) + 1,
        }}
        onClose={() => setUploadOpen(false)}
        onChanged={refresh}
        onPublish={(ready) => {
          refresh();
          const version = versions.find((v) => v.id === ready.versionId);
          if (version) setPublishing(version);
          else toast("success", `Version ${ready.versionNumber} is ready. Publish it from the timeline.`);
        }}
      />
      <PublishDialog
        document={document}
        version={publishing}
        active={active}
        onClose={() => setPublishing(null)}
        onDone={(text) => {
          toast("success", text);
          refresh();
        }}
      />
      <DeleteDialog
        open={deleting}
        document={document}
        onClose={() => setDeleting(false)}
        onDeleted={() => router.push("/admin/documents")}
      />
    </>
  );
}

// ---------- tabs (WAI-ARIA tabs pattern: arrow keys move between tabs) ----------

const TABS: { key: Tab; label: string }[] = [
  { key: "preview", label: "Preview" },
  { key: "chunks", label: "Chunks" },
  { key: "activity", label: "Activity" },
];

function Tabs({ tab, onChange }: { tab: Tab; onChange: (tab: Tab) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  function onKey(event: KeyboardEvent, index: number) {
    const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    event.preventDefault();
    const next = (index + delta + TABS.length) % TABS.length;
    onChange(TABS[next].key);
    refs.current[next]?.focus();
  }
  return (
    <div role="tablist" aria-label="Document views" className="flex gap-1 border-b border-border">
      {TABS.map((t, i) => (
        <button
          key={t.key}
          ref={(el) => {
            refs.current[i] = el;
          }}
          id={`tab-${t.key}`}
          role="tab"
          type="button"
          aria-selected={tab === t.key}
          aria-controls={`panel-${t.key}`}
          tabIndex={tab === t.key ? 0 : -1}
          onClick={() => onChange(t.key)}
          onKeyDown={(e) => onKey(e, i)}
          className={`-mb-px min-h-11 border-b-2 px-4 text-sm font-semibold transition-colors duration-150 ${
            tab === t.key ? "border-accent text-accent" : "border-transparent text-fg-muted hover:text-fg"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ---------- preview ----------

function PreviewPane({ document, version }: { document: DocumentDetail; version: Version }) {
  const [state, setState] = useState<{ id: string; preview: Preview | null; failed: boolean } | null>(null);
  useEffect(() => {
    let live = true;
    fetch(`/api/documents/${document.id}/preview?version_id=${version.id}`)
      .then(async (r) => {
        if (!live) return;
        setState({ id: version.id, preview: r.ok ? ((await r.json()) as Preview) : null, failed: !r.ok });
      })
      .catch(() => live && setState({ id: version.id, preview: null, failed: true }));
    return () => {
      live = false;
    };
  }, [document.id, version.id]);

  if (!state || state.id !== version.id) {
    return (
      <p className="flex items-center gap-2 text-sm text-fg-muted">
        <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
        Loading version {version.version_number}…
      </p>
    );
  }
  if (!state.preview) {
    return <p className="text-sm text-fg-muted">The original file of version {version.version_number} isn&apos;t available.</p>;
  }
  const inline = ["application/pdf", "text/plain"].includes(state.preview.mime_type);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-fg-muted">
          Version {version.version_number} · {version.original_filename}
          {/* From the refreshed document, not the preview fetch: publishing another version changes it. */}
          {document.active_version_number === version.version_number ? " · the published version" : " · not published"}
        </p>
        <a
          href={state.preview.url}
          target="_blank"
          rel="noopener noreferrer"
          className={buttonClass.secondary}
        >
          {inline ? <ExternalLink className="size-4" aria-hidden="true" /> : <Download className="size-4" aria-hidden="true" />}
          {inline ? "Open in a new tab" : "Download"}
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      </div>
      {inline ? (
        <iframe
          src={state.preview.url}
          title={`${document.title}, version ${version.version_number}`}
          className="h-[70dvh] w-full rounded-control border border-border bg-surface-muted"
        />
      ) : (
        <p className="text-sm text-fg-muted">Word files can&apos;t be shown in the browser. Download it to check it.</p>
      )}
    </div>
  );
}

// ---------- chunk diagnostics ----------

function ChunksPane({ documentId, version }: { documentId: string; version: Version }) {
  const [report, setReport] = useState<{ id: string; data: ChunkReport | null } | null>(null);
  useEffect(() => {
    let live = true;
    fetch(`/api/admin/documents/${documentId}/chunks?version_id=${version.id}`)
      .then(async (r) => live && setReport({ id: version.id, data: r.ok ? ((await r.json()) as ChunkReport) : null }))
      .catch(() => live && setReport({ id: version.id, data: null }));
    return () => {
      live = false;
    };
  }, [documentId, version.id]);

  if (!report || report.id !== version.id) {
    return (
      <p className="flex items-center gap-2 text-sm text-fg-muted">
        <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
        Checking both stores…
      </p>
    );
  }
  const data = report.data;
  if (!data) return <p className="text-sm text-fg-muted">Chunk diagnostics are unavailable right now.</p>;
  if (data.postgres_chunks === 0 && data.chroma_chunks === 0) {
    return <p className="text-sm text-fg-muted">Version {version.version_number} has no chunks yet.</p>;
  }
  return (
    <div className="space-y-3">
      {data.consistent ? (
        <p className="flex items-center gap-2 text-sm font-semibold">
          <BadgeCheck className="size-4 text-success" aria-hidden="true" />
          All {data.postgres_chunks} chunks are in the database and the search index.
        </p>
      ) : (
        <p role="alert" className="flex items-start gap-2 text-sm font-semibold">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
          The stores disagree: expected {data.expected ?? "?"}, database {data.postgres_chunks}, search index{" "}
          {data.chroma_chunks}. Re-index this version (upload it again or run the rebuild).
        </p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-sm">
          <caption className="sr-only">Chunks of version {version.version_number}</caption>
          <thead className="bg-surface-muted text-xs text-fg-muted">
            <tr>
              {["#", "Pages", "Section", "Tokens", "In index", "Text"].map((h) => (
                <th key={h} scope="col" className="px-3 py-2 font-semibold">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {data.chunks.map((chunk) => (
              <tr key={chunk.chroma_id} className="align-top">
                <td className="px-3 py-2 tabular-nums">{chunk.chunk_index}</td>
                <td className="px-3 py-2 whitespace-nowrap tabular-nums">
                  {chunk.page_start === chunk.page_end ? chunk.page_start : `${chunk.page_start}–${chunk.page_end}`}
                </td>
                <td className="max-w-48 px-3 py-2">{chunk.section?.split(" > ").at(-1) ?? "—"}</td>
                <td className="px-3 py-2 tabular-nums">{chunk.token_count ?? "—"}</td>
                <td className="px-3 py-2 whitespace-nowrap">
                  {chunk.in_index ? (
                    <span className="inline-flex items-center gap-1">
                      <BadgeCheck className="size-4 text-success" aria-hidden="true" />
                      Yes
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 font-semibold">
                      <TriangleAlert className="size-4 text-danger" aria-hidden="true" />
                      Missing
                    </span>
                  )}
                </td>
                <td className="px-3 py-2">
                  <p className="line-clamp-2 max-w-[60ch]">{chunk.preview ?? "—"}</p>
                  <p className="mt-1 font-mono text-xs break-all text-fg-muted">{chunk.chroma_id}</p>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------- activity (audit log) ----------

const ACTIONS: Record<string, string> = {
  "document.version.uploaded": "Uploaded version",
  "document.version.published": "Published version",
  "document.deleted": "Deleted the document",
  "document.purged": "Purged the document's data",
  "ingestion.cancelled": "Cancelled processing",
  "ingestion.retried": "Retried processing",
  "document.metadata_updated": "Edited details",
};

const FIELD_NAMES: Record<string, string> = {
  category: "category",
  tags: "tags",
  brand: "brand",
  department: "department",
  language: "language",
  effective_date: "effective date",
};

function ActivityList({ events, timezone }: { events: ActivityEvent[]; timezone: string }) {
  if (events.length === 0) return <p className="text-sm text-fg-muted">No activity recorded yet.</p>;
  return (
    <ol className="divide-y divide-border">
      {events.map((event) => {
        const number = event.details.version_number;
        return (
          <li key={event.id} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-3">
            <p className="text-sm">
              <span className="font-semibold">{event.actor}</span> {(ACTIONS[event.action] ?? event.action).toLowerCase()}
              {typeof number === "number" ? ` ${number}` : ""}
              {typeof event.details.filename === "string" ? ` (${event.details.filename})` : ""}
              {event.action === "document.metadata_updated"
                ? ` (${Object.keys(event.details)
                    .map((key) => FIELD_NAMES[key] ?? key)
                    .join(", ")})`
                : ""}
            </p>
            <p className="text-xs text-fg-muted">
              <time dateTime={event.created_at}>{absoluteTime(event.created_at, timezone)}</time>
              {event.request_id ? <span className="ml-2 font-mono">{event.request_id}</span> : null}
            </p>
          </li>
        );
      })}
    </ol>
  );
}

// ---------- publish (with publication warnings) and delete ----------

function PublishDialog({
  document,
  version,
  active,
  onClose,
  onDone,
}: {
  document: DocumentDetail;
  version: Version | null;
  active: Version | null;
  onClose: () => void;
  onDone: (text: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function publish() {
    if (!version) return;
    setBusy(true);
    setError(null);
    const response = await fetch(`/api/admin/documents/${document.id}/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version_id: version.id }),
    }).catch(() => null);
    setBusy(false);
    if (!response?.ok) {
      const failure = response ? ((await response.json()) as { error: ApiError }).error : null;
      setError(
        failure?.code === "VERSION_NOT_FULLY_INDEXED"
          ? `Version ${version.version_number} isn't fully in the search index (${failure.message}), so it wasn't published. Check the Chunks tab, then upload it again.`
          : (failure?.message ?? "That didn't reach us. Try again."),
      );
      return;
    }
    onClose();
    onDone(`Version ${version.version_number} of "${document.title}" is live.`);
  }

  const n = version?.version_number;
  const warnings: string[] = [];
  if (version?.job) warnings.push(...version.job.warnings.map(describeWarning));
  if (
    version &&
    active?.expected_chunk_count &&
    version.expected_chunk_count !== null &&
    version.expected_chunk_count < active.expected_chunk_count / 2
  ) {
    warnings.push(
      `Version ${n} has ${chunks(version.expected_chunk_count)}; version ${active.version_number} has ${chunks(active.expected_chunk_count)}. Check that nothing is missing.`,
    );
  }
  const employee = document.audience === "employee";

  return (
    <Modal
      open={version !== null}
      onClose={() => {
        setError(null);
        onClose();
      }}
      title={version ? `Publish version ${n} of "${document.title}"?` : ""}
      wide
    >
      <p className="text-sm">
        {active
          ? `New answers will cite version ${n}. Version ${active.version_number} will stop appearing in answers.`
          : "New answers can cite it from now on."}
      </p>
      <p className="mt-3 flex items-start gap-2 rounded-control bg-surface-muted p-3 text-sm">
        {employee ? (
          <Lock className="mt-0.5 size-4 shrink-0 text-fg-muted" aria-hidden="true" />
        ) : (
          <Globe className="mt-0.5 size-4 shrink-0 text-fg-muted" aria-hidden="true" />
        )}
        {employee
          ? "Employees only: signed-in staff see answers from it. Customers and the voice assistant never do."
          : "Public: customers, staff and the voice assistant will all see answers from it."}
      </p>
      {warnings.length ? (
        <ul className="mt-3 space-y-2" aria-label="Warnings">
          {warnings.map((w) => (
            <li key={w} className="flex items-start gap-2 rounded-control border border-warning/40 bg-warning/8 p-3 text-sm">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
              {w}
            </li>
          ))}
        </ul>
      ) : null}
      {error ? (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      ) : null}
      <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={onClose} className={buttonClass.secondary}>
          {active ? `Keep version ${active.version_number}` : "Not yet"}
        </button>
        <button type="button" disabled={busy} onClick={publish} className={buttonClass.primary}>
          {busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : null}
          Publish version {n}
        </button>
      </div>
    </Modal>
  );
}

function DeleteDialog({
  open,
  document,
  onClose,
  onDeleted,
}: {
  open: boolean;
  document: DocumentDetail;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function remove() {
    setBusy(true);
    const response = await fetch(`/api/admin/documents/${document.id}`, { method: "DELETE" }).catch(() => null);
    setBusy(false);
    if (!response?.ok) {
      setError(response ? ((await response.json()) as { error: ApiError }).error.message : "That didn't reach us. Try again.");
      return;
    }
    onDeleted();
  }
  return (
    <Modal open={open} onClose={onClose} title={`Delete "${document.title}"?`}>
      <p className="text-sm">It stops appearing in answers immediately. The deletion is logged.</p>
      {error ? (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      ) : null}
      <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button type="button" onClick={onClose} className={buttonClass.secondary}>
          Cancel
        </button>
        <button type="button" disabled={busy} onClick={remove} className={buttonClass.danger}>
          {busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : null}
          Delete document
        </button>
      </div>
    </Modal>
  );
}

// ---------- edit details (§2.2 metadata, ADR-028) ----------

// Product model and audience are copied into the search index at upload, so they aren't editable here.
function DetailsDialog({
  document,
  onClose,
  onSaved,
}: {
  document: DocumentDetail;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function save(form: HTMLFormElement) {
    const data = new FormData(form);
    const text = (name: string) => String(data.get(name) ?? "").trim() || null;
    const body = {
      category: text("category"),
      brand: text("brand"),
      department: text("department"),
      language: text("language") ?? "en",
      effective_date: text("effective_date"),
      tags: String(data.get("tags") ?? "")
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
    };
    setSaving(true);
    setError(null);
    const response = await fetch(`/api/admin/documents/${document.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).catch(() => null);
    setSaving(false);
    if (response?.ok) {
      onSaved();
      return;
    }
    const payload = response ? ((await response.json().catch(() => null)) as { error?: ApiError } | null) : null;
    setError(payload?.error?.message ?? "That didn't reach us. Try again.");
  }

  const input =
    "mt-1.5 block min-h-11 w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg";
  return (
    <Modal
      open
      onClose={onClose}
      title={`Details of "${document.title}"`}
      description="Used to find and filter documents. Product model and audience are set when you upload."
    >
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          void save(event.currentTarget);
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-semibold">
            Category
            <input name="category" defaultValue={document.category ?? ""} maxLength={120} className={input} />
          </label>
          <label className="block text-sm font-semibold">
            Brand
            <input name="brand" defaultValue={document.brand ?? ""} maxLength={120} className={input} />
          </label>
          <label className="block text-sm font-semibold">
            Department
            <input name="department" defaultValue={document.department ?? ""} maxLength={120} className={input} />
          </label>
          <label className="block text-sm font-semibold">
            Effective date
            <input name="effective_date" type="date" defaultValue={document.effective_date ?? ""} className={input} />
          </label>
          <label className="block text-sm font-semibold">
            Language
            <select name="language" defaultValue={document.language} className={input}>
              <option value="en">English</option>
              <option value="bn">Bangla</option>
            </select>
          </label>
          <label className="block text-sm font-semibold">
            Tags
            <input
              name="tags"
              defaultValue={document.tags.join(", ")}
              maxLength={400}
              className={input}
              aria-describedby="details-tags-hint"
            />
            <span id="details-tags-hint" className="mt-1 block text-xs font-normal text-fg-muted">
              Separate with commas, up to 10
            </span>
          </label>
        </div>
        {error ? (
          <p role="alert" className="flex items-start gap-2 text-sm text-danger">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span className="text-fg">{error}</span>
          </p>
        ) : null}
        <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
          <button type="button" onClick={onClose} className={buttonClass.secondary}>
            Cancel
          </button>
          <button type="submit" disabled={saving} className={buttonClass.primary}>
            {saving ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : null}
            {saving ? "Saving…" : "Save details"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
