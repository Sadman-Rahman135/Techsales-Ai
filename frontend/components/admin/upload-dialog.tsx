"use client";

import { CircleCheck, CircleDashed, FileUp, Info, LoaderCircle, RotateCw, TriangleAlert, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type DragEvent, type FormEvent } from "react";

import { buttonClass, Modal } from "@/components/admin/modal";
import {
  ACCEPTED_TYPES,
  type ApiError,
  FINISHED,
  formatBytes,
  type Job,
  MAX_UPLOAD_MB,
  STAGES,
  type UploadAccepted,
} from "@/lib/documents";

const POLL_MS = 2000;
const EXTENSIONS = ACCEPTED_TYPES.split(",");

type Tracking = { accepted: UploadAccepted; title: string; job: Job };

export type ReadyVersion = { documentId: string; versionId: string; versionNumber: number; title: string };

// `target` set: upload a new version of that document (its title, audience and tags stay as they are).
export type VersionTarget = { documentId: string; title: string; nextVersion: number };

export function UploadDialog({
  open,
  onClose,
  onChanged,
  onPublish,
  target,
}: {
  open: boolean;
  onClose: () => void;
  onChanged: () => void;
  onPublish: (version: ReadyVersion) => void;
  target?: VersionTarget;
}) {
  const [tracking, setTracking] = useState<Tracking | null>(null);

  function close() {
    onClose();
    setTracking(null); // next open starts at step 1; a running job keeps going and shows in the list
  }

  return (
    <Modal
      open={open}
      onClose={close}
      wide
      title={
        tracking
          ? `Processing "${tracking.title}"`
          : target
            ? `Upload version ${target.nextVersion} of "${target.title}"`
            : "Upload document"
      }
      description={
        tracking
          ? "You can close this window. Processing continues and the list shows its status."
          : target
            ? "The published version keeps answering questions until you publish the new one."
            : "Approved manuals and policies only. New documents stay private until you publish them. To update a document, open it and upload a new version."
      }
    >
      {tracking ? (
        <IngestionTracker
          tracking={tracking}
          onJob={(job) => setTracking({ ...tracking, job })}
          onChanged={onChanged}
          onClose={close}
          onChooseAnother={() => setTracking(null)}
          onPublish={() => {
            onPublish({
              documentId: tracking.accepted.document_id,
              versionId: tracking.accepted.version_id,
              versionNumber: tracking.accepted.version_number,
              title: tracking.title,
            });
            close();
          }}
        />
      ) : (
        <UploadForm
          target={target}
          onAccepted={(accepted, title) => {
            setTracking({ accepted, title, job: accepted.job });
            onChanged();
          }}
          onCancel={close}
        />
      )}
    </Modal>
  );
}

// ---------- step 1: file and metadata ----------

function UploadForm({
  target,
  onAccepted,
  onCancel,
}: {
  target?: VersionTarget;
  onAccepted: (accepted: UploadAccepted, title: string) => void;
  onCancel: () => void;
}) {
  const id = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const router = useRouter();

  function choose(candidate: File | undefined) {
    setError(null);
    if (!candidate) return;
    const extension = candidate.name.slice(candidate.name.lastIndexOf(".")).toLowerCase();
    if (!EXTENSIONS.includes(extension)) {
      setError({
        code: "FILE_TYPE_NOT_ALLOWED",
        message: "This file type isn't supported. Upload a PDF, Word (.docx) or text (.txt) file.",
        request_id: "",
      });
      return;
    }
    if (candidate.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setError({
        code: "FILE_TOO_LARGE",
        message: `This file is larger than ${MAX_UPLOAD_MB} MB. Split it or upload a smaller file.`,
        request_id: "",
      });
      return;
    }
    setFile(candidate);
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files[0]);
  }

  // A new version of an existing document: the dialog's target, or the one chosen after a title clash (kept, so
  // "upload anyway" after a duplicate-file warning still goes to that document).
  const [chosen, setChosen] = useState<VersionTarget | null>(null);
  const versionTarget = chosen ?? target ?? null;

  async function send(
    formElement: HTMLFormElement,
    options: { allowDuplicate?: boolean; allowSameTitle?: boolean; versionOf?: VersionTarget } = {},
  ) {
    if (!file) {
      setError({ code: "FILE_REQUIRED", message: "Choose a file to upload.", request_id: "" });
      return;
    }
    if (options.versionOf) setChosen(options.versionOf);
    const versionOf = options.versionOf ?? versionTarget;
    const form = new FormData(formElement);
    const body = new FormData();
    body.append("file", file);
    if (!versionOf) {
      for (const field of [
        "title",
        "audience",
        "product_model",
        "category",
        "tags",
        "brand",
        "department",
        "language",
        "effective_date",
      ]) {
        const value = String(form.get(field) ?? "").trim();
        if (value) body.append(field, value);
      }
      if (options.allowSameTitle) body.append("allow_same_title", "true");
    }
    if (options.allowDuplicate) body.append("allow_duplicate", "true");

    setSubmitting(true);
    setError(null);
    try {
      const url = versionOf ? `/api/admin/documents/${versionOf.documentId}/versions` : "/api/admin/documents";
      const response = await fetch(url, { method: "POST", body });
      if (response.status === 401) {
        const back = versionOf ? `/admin/documents/${versionOf.documentId}` : "/admin/documents";
        router.push(`/login?next=${encodeURIComponent(back)}`);
        return;
      }
      const payload = (await response.json()) as UploadAccepted | { error: ApiError };
      if ("error" in payload) {
        setError(payload.error);
        return;
      }
      onAccepted(payload, versionOf?.title ?? (String(form.get("title") ?? "").trim() || titleFromFile(file.name)));
    } catch {
      setError({
        code: "NETWORK",
        message: "The upload didn't reach us. Check your connection and try again.",
        request_id: "",
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      ref={formRef}
      onSubmit={(event: FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        void send(event.currentTarget);
      }}
      noValidate
      className="space-y-5"
    >
      <div>
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`flex flex-col items-center gap-3 rounded-card border-2 border-dashed px-4 py-6 text-center transition-colors duration-150 ${
            dragging ? "border-accent bg-accent/8" : "border-border-strong"
          }`}
        >
          <FileUp className="size-6 text-fg-muted" aria-hidden="true" />
          {file ? (
            <p className="text-sm">
              <span className="font-semibold">{file.name}</span>{" "}
              <span className="text-fg-muted">· {formatBytes(file.size)}</span>
            </p>
          ) : (
            <p className="text-sm text-fg-muted">Drag a file here, or</p>
          )}
          <button type="button" onClick={() => inputRef.current?.click()} className={buttonClass.secondary}>
            {file ? "Choose a different file" : "Choose file"}
          </button>
          <input
            ref={inputRef}
            id={`${id}-file`}
            type="file"
            accept={ACCEPTED_TYPES}
            className="sr-only"
            tabIndex={-1}
            aria-label="File to upload"
            onChange={(e) => choose(e.target.files?.[0])}
          />
        </div>
        <p className="mt-2 text-xs text-fg-muted">
          PDF, Word (.docx) or text (.txt), up to {MAX_UPLOAD_MB} MB. Scanned PDFs without text can&apos;t be read yet.
        </p>
      </div>

      {target ? null : <DocumentFields id={id} />}

      {error ? (
        <div role="alert" className="rounded-control border border-danger/40 bg-danger/8 p-3 text-sm">
          <p className="flex items-start gap-2 text-danger">
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span className="text-fg">{error.message}</span>
          </p>
          {error.code === "DUPLICATE_FILE" && error.details ? (
            <button
              type="button"
              className={`${buttonClass.quiet} mt-2`}
              onClick={() => formRef.current && void send(formRef.current, { allowDuplicate: true })}
            >
              {versionTarget
                ? `Upload anyway as version ${versionTarget.nextVersion}`
                : "Upload anyway as a separate document"}
            </button>
          ) : null}
          {error.code === "DOCUMENT_TITLE_EXISTS" && error.details ? (
            <TitleClash
              details={error.details}
              disabled={submitting}
              onNewVersion={(versionOf) => formRef.current && void send(formRef.current, { versionOf })}
              onSeparate={() => formRef.current && void send(formRef.current, { allowSameTitle: true })}
            />
          ) : null}
          {error.request_id && error.request_id !== "bff" ? (
            <p className="mt-1 font-mono text-xs text-fg-muted">Reference: {error.request_id}</p>
          ) : null}
        </div>
      ) : null}

      <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
        <button type="button" onClick={onCancel} className={buttonClass.secondary}>
          Cancel
        </button>
        <button type="submit" disabled={submitting} className={buttonClass.primary}>
          {submitting ? (
            <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <Upload className="size-4" aria-hidden="true" />
          )}
          {submitting ? "Uploading…" : "Upload and process"}
        </button>
      </div>
    </form>
  );
}

// A new document named like a live one is usually an update: offer the new version first.
function TitleClash({
  details,
  disabled,
  onNewVersion,
  onSeparate,
}: {
  details: Record<string, unknown>;
  disabled: boolean;
  onNewVersion: (versionOf: VersionTarget) => void;
  onSeparate: () => void;
}) {
  const documentId = String(details.document_id ?? "");
  const title = String(details.title ?? "");
  const next = Number(details.next_version ?? 0);
  if (!documentId || !title || !next) return null;
  return (
    <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
      <button
        type="button"
        disabled={disabled}
        className={buttonClass.primary}
        onClick={() => onNewVersion({ documentId, title, nextVersion: next })}
      >
        <Upload className="size-4" aria-hidden="true" />
        Upload as version {next} of &ldquo;{title}&rdquo;
      </button>
      <button type="button" disabled={disabled} className={buttonClass.secondary} onClick={onSeparate}>
        Keep as a separate document
      </button>
    </div>
  );
}

function DocumentFields({ id }: { id: string }) {
  return (
    <>
      <Field id={`${id}-title`} label="Title" hint="Shown in citations. Leave empty to use the file name.">
        <input
          id={`${id}-title`}
          aria-describedby={`${id}-title-hint`}
          name="title"
          maxLength={200}
          className={inputClass}
          placeholder="e.g. Laptop warranty"
        />
      </Field>

      <fieldset>
        <legend className="text-sm font-semibold">Who can see answers from it</legend>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <Radio name="audience" value="public" defaultChecked label="Public" hint="Customers and staff" />
          <Radio name="audience" value="employee" label="Employees only" hint="Never shown to customers" />
        </div>
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field id={`${id}-model`} label="Product model" hint="Optional">
          <input
            id={`${id}-model`}
            aria-describedby={`${id}-model-hint`}
            name="product_model"
            maxLength={120}
            className={inputClass}
            placeholder="e.g. TC-Book 14"
          />
        </Field>
        <Field id={`${id}-category`} label="Category" hint="Optional">
          <input
            id={`${id}-category`}
            aria-describedby={`${id}-category-hint`}
            name="category"
            maxLength={120}
            className={inputClass}
            placeholder="e.g. Warranty"
          />
        </Field>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field id={`${id}-brand`} label="Brand" hint="Optional">
          <input
            id={`${id}-brand`}
            aria-describedby={`${id}-brand-hint`}
            name="brand"
            maxLength={120}
            className={inputClass}
            placeholder="e.g. TechCare"
          />
        </Field>
        <Field id={`${id}-department`} label="Department" hint="Optional. The team that owns it">
          <input
            id={`${id}-department`}
            aria-describedby={`${id}-department-hint`}
            name="department"
            maxLength={120}
            className={inputClass}
            placeholder="e.g. Repairs"
          />
        </Field>
        <Field id={`${id}-effective`} label="Effective date" hint="Optional. When this version takes effect">
          <input
            id={`${id}-effective`}
            aria-describedby={`${id}-effective-hint`}
            name="effective_date"
            type="date"
            className={inputClass}
          />
        </Field>
        <Field id={`${id}-language`} label="Language" hint="Answers are in English for now">
          <select
            id={`${id}-language`}
            aria-describedby={`${id}-language-hint`}
            name="language"
            defaultValue="en"
            className={inputClass}
          >
            <option value="en">English</option>
            <option value="bn">Bangla</option>
          </select>
        </Field>
      </div>

      <Field id={`${id}-tags`} label="Tags" hint="Optional. Separate with commas, up to 10 (e.g. returns, store policy)">
        <input
          id={`${id}-tags`}
          aria-describedby={`${id}-tags-hint`}
          name="tags"
          maxLength={400}
          className={inputClass}
          placeholder="e.g. returns, store policy"
        />
      </Field>
    </>
  );
}

const inputClass =
  "mt-1.5 block min-h-11 w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg placeholder:text-fg-muted/70";

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  // The hint is the field's description, not part of its name.
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-semibold">
        {label}
      </label>
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-fg-muted">
          {hint}
        </p>
      ) : null}
      {children}
    </div>
  );
}

function Radio({
  name,
  value,
  label,
  hint,
  defaultChecked,
}: {
  name: string;
  value: string;
  label: string;
  hint: string;
  defaultChecked?: boolean;
}) {
  return (
    <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-control border border-border-strong p-3 has-[:checked]:border-accent has-[:checked]:bg-accent/8">
      <input
        type="radio"
        name={name}
        value={value}
        defaultChecked={defaultChecked}
        className="mt-1 size-4 accent-accent"
      />
      <span>
        <span className="block text-sm font-semibold">{label}</span>
        <span className="block text-xs text-fg-muted">{hint}</span>
      </span>
    </label>
  );
}

function titleFromFile(name: string): string {
  return (
    name
      .replace(/\.[^.]+$/, "")
      .replace(/[_-]+/g, " ")
      .trim() || "Untitled document"
  );
}

// ---------- step 2: progress stepper ----------

type StepState = "done" | "current" | "failed" | "upcoming";

function stepStates(job: Job): StepState[] {
  if (job.state === "done") return STAGES.map(() => "done");
  if (job.state === "failed" || job.state === "cancelled") {
    const failedAt = Math.max(
      1,
      STAGES.findIndex((s) => s.key === job.stage),
    );
    return STAGES.map((_, i) => (i < failedAt ? "done" : i === failedAt ? "failed" : "upcoming"));
  }
  const current = job.state === "queued" ? 1 : STAGES.findIndex((s) => s.key === job.state);
  return STAGES.map((_, i) => (i < current ? "done" : i === current ? "current" : "upcoming"));
}

const STEP_TEXT: Record<StepState, string> = {
  done: "completed",
  current: "in progress",
  failed: "failed",
  upcoming: "not started",
};

function IngestionTracker({
  tracking,
  onJob,
  onChanged,
  onClose,
  onChooseAnother,
  onPublish,
}: {
  tracking: Tracking;
  onJob: (job: Job) => void;
  onChanged: () => void;
  onClose: () => void;
  onChooseAnother: () => void;
  onPublish: () => void;
}) {
  const { job } = tracking;
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const finished = FINISHED.has(job.state);

  useEffect(() => {
    if (finished) {
      onChanged(); // refresh the list once when the job settles
      return;
    }
    const timer = setInterval(async () => {
      try {
        const response = await fetch(`/api/admin/ingestion-jobs/${job.id}`, { cache: "no-store" });
        if (response.ok) onJob((await response.json()) as Job);
      } catch {
        // transient network blip: the next poll tries again
      }
    }, POLL_MS);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll per job id and state, not per callback identity
  }, [job.id, finished]);

  async function act(action: "retry" | "cancel") {
    setBusy(true);
    setActionError(null);
    try {
      const response = await fetch(`/api/admin/ingestion-jobs/${job.id}/${action}`, { method: "POST" });
      const payload = (await response.json()) as Job | { error: ApiError };
      if ("error" in payload) setActionError(payload.error.message);
      else onJob(payload);
    } catch {
      setActionError("That didn't reach us. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const states = stepStates(job);
  const retrying = job.state === "queued" && job.error_code !== null;

  return (
    <div className="space-y-5">
      <ol className="grid gap-2 sm:grid-cols-6 sm:gap-1" aria-label="Processing steps">
        {STAGES.map((stage, i) => {
          const state = states[i];
          const Icon =
            state === "done"
              ? CircleCheck
              : state === "failed"
                ? TriangleAlert
                : state === "current"
                  ? LoaderCircle
                  : CircleDashed;
          const tone =
            state === "done"
              ? "text-success"
              : state === "failed"
                ? "text-danger"
                : state === "current"
                  ? "text-accent"
                  : "text-fg-muted";
          return (
            <li
              key={stage.key}
              aria-current={state === "current" || state === "failed" ? "step" : undefined}
              className="flex items-center gap-3 sm:flex-col sm:gap-1.5 sm:text-center"
            >
              <span
                className={`relative grid size-8 shrink-0 place-items-center rounded-full border ${state === "upcoming" ? "border-border" : "border-current"} ${tone}`}
              >
                <Icon className={`size-4 ${state === "current" ? "animate-spin" : ""}`} aria-hidden="true" />
              </span>
              <span className={`text-sm ${state === "upcoming" ? "text-fg-muted" : "font-semibold"}`}>
                {stage.label}
                <span className="sr-only"> ({STEP_TEXT[state]})</span>
              </span>
            </li>
          );
        })}
      </ol>

      <div aria-live="polite" className="rounded-control bg-surface-muted p-3 text-sm">
        {job.state === "done" ? (
          <p className="flex items-start gap-2">
            <CircleCheck className="mt-0.5 size-4 shrink-0 text-success" aria-hidden="true" />
            <span>Ready to publish. It isn&apos;t answering questions yet; publishing makes it live.</span>
          </p>
        ) : job.state === "failed" ? (
          <p className="flex items-start gap-2">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
            <span>{job.error_message ?? "Processing failed."}</span>
          </p>
        ) : job.state === "cancelled" ? (
          <p>Processing was cancelled. Nothing from this file is searchable.</p>
        ) : retrying ? (
          <p className="flex items-start gap-2">
            <RotateCw className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
            <span>
              {job.error_message} Attempt {job.attempts + 1} of {job.max_attempts}.
            </span>
          </p>
        ) : (
          <p>
            {job.cancel_requested
              ? "Stopping after the current step…"
              : job.state === "queued"
                ? "Uploaded. Waiting for a processing slot…"
                : `${STAGES[states.indexOf("current")]?.label ?? "Working"}…`}
          </p>
        )}
      </div>

      {job.state === "done" && job.warnings.length > 0 ? (
        <ul className="space-y-1.5 text-sm">
          {job.warnings.map((warning) => (
            <li key={warning.code} className="flex items-start gap-2">
              <Info className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
              {describeWarning(warning)}
            </li>
          ))}
        </ul>
      ) : null}

      {actionError ? (
        <p role="alert" className="text-sm text-danger">
          {actionError}
        </p>
      ) : null}

      <div className="flex flex-col-reverse gap-2 border-t border-border pt-4 sm:flex-row sm:justify-end">
        {!finished ? (
          <>
            <button
              type="button"
              disabled={busy || job.cancel_requested}
              onClick={() => act("cancel")}
              className={buttonClass.secondary}
            >
              Cancel processing
            </button>
            <button type="button" onClick={onClose} className={buttonClass.primary}>
              Close and keep processing
            </button>
          </>
        ) : job.state === "done" ? (
          <>
            <button type="button" onClick={onClose} className={buttonClass.secondary}>
              Publish later
            </button>
            <button type="button" onClick={onPublish} className={buttonClass.primary}>
              Review and publish
            </button>
          </>
        ) : (
          <>
            <button type="button" onClick={onChooseAnother} className={buttonClass.secondary}>
              Choose another file
            </button>
            <button type="button" disabled={busy} onClick={() => act("retry")} className={buttonClass.primary}>
              <RotateCw className="size-4" aria-hidden="true" />
              Retry
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export function describeWarning(warning: Job["warnings"][number]): string {
  const pages = warning.pages?.join(", ");
  switch (warning.code) {
    case "PAGES_WITHOUT_TEXT":
      return `No text on page ${pages}; those pages were skipped. Scanned pages need OCR, which isn't available yet.`;
    case "PAGES_LOW_CONFIDENCE":
      return `Page ${pages} may not have been read correctly. Check answers that cite it.`;
    case "REDACTED": {
      const parts = Object.entries(warning.counts ?? {}).map(([kind, n]) => `${n} ${kind}${n === 1 ? "" : "s"}`);
      return `Removed before indexing so they can never appear in answers: ${parts.join(", ")}.`;
    }
    default:
      return warning.code;
  }
}
