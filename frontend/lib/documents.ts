// API shapes for the admin document center (backend/app/api/documents.py). Safe to import from client code.

export type Audience = "public" | "employee";
export type JobState = "queued" | "parsing" | "chunking" | "embedding" | "indexing" | "done" | "failed" | "cancelled";
export type UploadStage = "uploaded" | "parsing" | "chunking" | "embedding" | "indexing" | "ready";
export type DocumentStatus = "processing" | "failed" | "cancelled" | "ready" | "published" | "archived" | "draft";

export type Job = {
  id: string;
  version_id: string;
  state: JobState;
  stage: UploadStage;
  attempts: number;
  max_attempts: number;
  error_code: string | null;
  error_message: string | null;
  failed_stage: string | null;
  cancel_requested: boolean;
  warnings: { code: string; pages?: number[]; counts?: Record<string, number> }[];
  stage_timings: Record<string, number>;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  run_after: string;
};

export type Version = {
  id: string;
  version_number: number;
  status: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  expected_chunk_count: number | null;
  indexed_chunk_count: number;
  created_at: string;
  published_at: string | null;
  job: Job | null;
};

export type DocumentSummary = {
  id: string;
  title: string;
  audience: Audience;
  category: string | null;
  product_model: string | null;
  tags: string[];
  brand: string | null;
  department: string | null;
  language: string;
  effective_date: string | null; // ISO date
  owner: string | null;
  status: DocumentStatus;
  active_version_number: number | null;
  latest_version: Version | null;
  created_at: string;
  updated_at: string;
};

export type DocumentPage = { items: DocumentSummary[]; total: number; limit: number; offset: number };

export type DocumentDetail = DocumentSummary & { versions: Version[] };

// Metadata search on the documents list (§2.2 P1). Shared by the page (server) and the list (client).
export const METADATA_FILTERS = ["brand", "category", "department", "language", "from", "to"] as const;

export type Facets = {
  product_models: string[];
  tags: string[];
  brands?: string[];
  categories?: string[];
  departments?: string[];
  languages?: string[];
};

export type Chunk = {
  chunk_index: number;
  chroma_id: string;
  page_start: number;
  page_end: number;
  section: string | null;
  token_count: number | null;
  text_checksum: string;
  in_index: boolean;
  preview: string | null;
};

export type ChunkReport = {
  version_id: string;
  version_number: number;
  status: string;
  expected: number | null;
  postgres_chunks: number;
  chroma_chunks: number;
  consistent: boolean;
  chunks: Chunk[];
};

export type ActivityEvent = {
  id: string;
  action: string;
  actor: string;
  target_type: string;
  target_id: string | null;
  details: Record<string, unknown>;
  request_id: string | null;
  created_at: string;
};

export type Preview = {
  url: string;
  expires_at: string;
  document_id: string;
  version_id: string;
  version_number: number;
  title: string;
  filename: string;
  mime_type: string;
  page: number | null;
  is_active_version: boolean;
};

export type UploadAccepted = { document_id: string; version_id: string; version_number: number; job: Job };

export type ApiError = { code: string; message: string; request_id: string; details?: Record<string, unknown> };

export const FINISHED: ReadonlySet<JobState> = new Set(["done", "failed", "cancelled"]);

export const STAGES: { key: UploadStage; label: string }[] = [
  { key: "uploaded", label: "Uploaded" },
  { key: "parsing", label: "Parsing" },
  { key: "chunking", label: "Chunking" },
  { key: "embedding", label: "Embedding" },
  { key: "indexing", label: "Indexing" },
  { key: "ready", label: "Ready" },
];

// Upload limits shown before upload (design-system/pages/admin-documents.md). Mirrors backend defaults.
export const ACCEPTED_TYPES = ".pdf,.docx,.txt";
export const MAX_UPLOAD_MB = Number(process.env.NEXT_PUBLIC_MAX_UPLOAD_MB ?? 20);

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
