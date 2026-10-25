import { Activity, BadgeCheck, TriangleAlert } from "lucide-react";
import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { AdminShell } from "@/components/admin/admin-shell";
import { NoPermission } from "@/components/admin/no-permission";
import { absoluteTime } from "@/components/admin/when";
import { RangeControl } from "@/components/quality/controls";
import { canManageDocuments, requireUser } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";
import { resolveRange } from "@/lib/quality";

export const metadata: Metadata = { title: "Operations" };

const PATH = "/admin/operations";

// Blueprint §13.1 (ADR-021): how the system is doing, from GET /api/v1/admin/metrics. Quality (answers) lives on
// /admin/quality; this page is latency, cost, errors, ingestion and alerts. Tables only: no new chart colors.
type Rate = { count: number; of: number; rate: number | null };
type Percentiles = { p50: number | null; p95: number | null; n?: number };
type Alert = { id: string; rule: string; severity: string; summary: string; fired_at: string; resolved_at: string | null };
type Metrics = {
  range: { label: string; timezone: string };
  tenant: {
    questions: number;
    active: { signed_in_users: number; guest_sessions: number };
    by_caller: Record<string, number>;
    latency_ms: Percentiles & { by_node: Record<string, Percentiles> };
    tokens: Record<string, number> & { estimated_cost_usd: number; estimated_cost_per_question_usd: number | null };
    retrieval_zero_results: Rate;
    refusals: Rate;
    errors: Rate;
    error_codes: Record<string, number>;
    citation_failures: number;
    correction_reports: number;
    tool_calls: { count: number; failures: number };
    ingestion: {
      jobs: number;
      by_state: Record<string, number>;
      parse_failures: number;
      retries: number;
      embedding_failures: number;
      indexing_lag_ms: Percentiles;
    };
  };
  service: {
    requests: {
      total: number;
      server_errors: Rate;
      rate_limited: number;
      permission_denied: number;
      unauthenticated: number;
      top_failing_routes: Record<string, number>;
    };
    queue: { depth: number | null; oldest_wait_seconds: number | null };
    alerts: { open: Alert[]; recent: Alert[] };
  };
};

const NODE_LABEL: Record<string, string> = {
  validate_request: "Check the question",
  classify_intent: "Classify",
  retrieve_documents: "Search documents",
  grade_evidence: "Grade evidence",
  rewrite_query: "Rewrite (once)",
  generate_answer: "Write the answer",
  check_citations: "Check citations",
  abstain: "Not in documents",
  direct_reply: "Direct reply",
  business_tool: "Order handoff",
};

const order = (node: string) => {
  const i = Object.keys(NODE_LABEL).indexOf(node);
  return i === -1 ? 99 : i;
};

export default function OperationsPage({ searchParams }: PageProps<"/admin/operations">) {
  return (
    <AdminShell current={PATH}>
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 outline-none sm:px-6 lg:py-8">
        <h1 className="font-heading text-2xl font-semibold">Operations</h1>
        <p className="mt-1 text-sm text-fg-muted">
          Speed, cost, errors and the document pipeline. Answer quality is on the Quality page.
        </p>
        <Suspense fallback={<div aria-hidden="true" className="mt-6 h-96 animate-pulse rounded-card bg-surface-muted" />}>
          <Operations searchParams={searchParams} />
        </Suspense>
      </main>
    </AdminShell>
  );
}

function one(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

const pct = (r: Rate) => (r.rate === null ? "No data" : `${(r.rate * 100).toFixed(1)}%`);
const ms = (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${v} ms`);

async function Operations({ searchParams }: { searchParams: PageProps<"/admin/operations">["searchParams"] }) {
  const me = await requireUser(PATH);
  if (!canManageDocuments(me)) return <NoPermission what="see operations" />;
  const params = await searchParams;
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: me.organization.timezone }).format(new Date());
  const range = resolveRange({ range: one(params.range), start: one(params.start), end: one(params.end) }, today);
  const response = await backendFetch(`/api/v1/admin/metrics?start=${range.start}&end=${range.end}`);
  if (response.status === 401) redirect(`/login?next=${PATH}`);
  if (!response.ok) throw new Error(`metrics failed: HTTP ${response.status}`);
  const data = (await response.json()) as Metrics;
  const { tenant, service } = data;
  const requests = service.requests;

  return (
    <div className="mt-6 space-y-6">
      <RangeControl range={range} label={data.range.label} />

      <section aria-labelledby="alerts-title" className="rounded-card border border-border bg-surface p-4">
        <h2 id="alerts-title" className="font-heading text-base font-semibold">
          Alerts
        </h2>
        {service.alerts.open.length === 0 ? (
          <p className="mt-2 flex items-center gap-2 text-sm">
            <BadgeCheck className="size-4 text-success" aria-hidden="true" />
            No open alerts.
          </p>
        ) : (
          <ul className="mt-2 space-y-2" role="alert">
            {service.alerts.open.map((alert) => (
              <li key={alert.id} className="flex items-start gap-2 rounded-control border border-danger/40 bg-danger/8 p-3 text-sm">
                <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
                <span>
                  <span className="font-semibold capitalize">{alert.severity}:</span> {alert.summary}{" "}
                  <span className="text-fg-muted">since {absoluteTime(alert.fired_at, data.range.timezone)}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Key figures" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Tile label="Requests" value={String(requests.total)} note={`${requests.unauthenticated} not signed in (401)`} />
        <Tile
          label="Server errors"
          value={pct(requests.server_errors)}
          note={`${requests.server_errors.count} of ${requests.server_errors.of} requests`}
        />
        <Tile label="Rate-limited" value={String(requests.rate_limited)} note={`${requests.permission_denied} refused (403)`} />
        <Tile
          label="Answer time"
          value={ms(tenant.latency_ms.p50)}
          note={`median; 95th percentile ${ms(tenant.latency_ms.p95)}`}
        />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Answer pipeline, per step">
          <Table
            caption="Time per graph step"
            head={["Step", "Median", "95th percentile", "Runs"]}
            rows={Object.entries(tenant.latency_ms.by_node)
              .sort(([a], [b]) => order(a) - order(b))
              .map(([node, p]) => [
              NODE_LABEL[node] ?? node,
              ms(p.p50),
              ms(p.p95),
              String(p.n ?? 0),
            ])}
          />
        </Panel>
        <Panel title="Tokens and cost">
          <Table
            caption="Model usage"
            head={["Measure", "Value"]}
            rows={[
              ["Questions", String(tenant.questions)],
              ["Embedding tokens", String(tenant.tokens.embedding ?? 0)],
              ["Prompt tokens", String(tenant.tokens.prompt ?? 0)],
              ["Answer tokens", String(tenant.tokens.completion ?? 0)],
              ["Model calls", String(tenant.tokens.llm_calls ?? 0)],
              ["Estimated cost", `$${tenant.tokens.estimated_cost_usd.toFixed(4)}`],
              [
                "Estimated cost per question",
                tenant.tokens.estimated_cost_per_question_usd === null
                  ? "No data"
                  : `$${tenant.tokens.estimated_cost_per_question_usd.toFixed(6)}`,
              ],
            ]}
          />
          <p className="mt-2 text-xs text-fg-muted">From list prices in settings; the offline models cost $0.</p>
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Answer signals">
          <Table
            caption="Answer signals"
            head={["Signal", "Count", "Share"]}
            rows={[
              ["Searches that found nothing", String(tenant.retrieval_zero_results.count), pct(tenant.retrieval_zero_results)],
              ["Not in our documents", String(tenant.refusals.count), pct(tenant.refusals)],
              ["Failed answers", String(tenant.errors.count), pct(tenant.errors)],
              ["Citation check failures", String(tenant.citation_failures), "–"],
              ["Not helpful reports", String(tenant.correction_reports), "–"],
              ["Order handoffs (tool calls)", String(tenant.tool_calls.count), `${tenant.tool_calls.failures} failed`],
            ]}
          />
          {Object.keys(tenant.error_codes).length ? (
            <p className="mt-2 text-xs text-fg-muted">
              Codes:{" "}
              {Object.entries(tenant.error_codes)
                .map(([code, n]) => `${code} ${n}`)
                .join(" · ")}
            </p>
          ) : null}
        </Panel>
        <Panel title="Document pipeline">
          <Table
            caption="Ingestion"
            head={["Measure", "Value"]}
            rows={[
              ["Uploads processed", String(tenant.ingestion.jobs)],
              ["Finished", Object.entries(tenant.ingestion.by_state).map(([s, n]) => `${s} ${n}`).join(" · ") || "–"],
              ["Parse failures", String(tenant.ingestion.parse_failures)],
              ["Retries after a failure", String(tenant.ingestion.retries)],
              ["Embedding failures", String(tenant.ingestion.embedding_failures)],
              ["Upload to ready (median)", ms(tenant.ingestion.indexing_lag_ms.p50)],
              ["Upload to ready (95th percentile)", ms(tenant.ingestion.indexing_lag_ms.p95)],
              [
                "Queue now",
                service.queue.depth === null
                  ? "No worker heartbeat"
                  : `${service.queue.depth} waiting${service.queue.oldest_wait_seconds ? `, oldest ${service.queue.oldest_wait_seconds} s` : ""}`,
              ],
            ]}
          />
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Who asked">
          <Table
            caption="Questions by caller"
            head={["Caller", "Questions"]}
            rows={[
              ["Signed-in people", String(tenant.active.signed_in_users)],
              ["Guest sessions", String(tenant.active.guest_sessions)],
              ...Object.entries(tenant.by_caller).map(([role, n]) => [
                role === "unrecorded" ? "Before role tracking (older answers)" : `As ${role}`,
                String(n),
              ]),
            ]}
          />
        </Panel>
        <Panel title="Alert history">
          {service.alerts.recent.length === 0 ? (
            <p className="text-sm text-fg-muted">No alerts fired in this period.</p>
          ) : (
            <Table
              caption="Alerts in this period"
              head={["Fired", "Alert", "Resolved"]}
              rows={service.alerts.recent.map((a) => [
                absoluteTime(a.fired_at, data.range.timezone),
                a.summary,
                a.resolved_at ? absoluteTime(a.resolved_at, data.range.timezone) : "Open",
              ])}
            />
          )}
          {Object.keys(requests.top_failing_routes).length ? (
            <p className="mt-2 text-xs text-fg-muted">
              <Activity className="mr-1 inline size-3.5" aria-hidden="true" />
              Server errors by route:{" "}
              {Object.entries(requests.top_failing_routes)
                .map(([route, n]) => `${route} ${n}`)
                .join(" · ")}
            </p>
          ) : null}
        </Panel>
      </div>
    </div>
  );
}

function Tile({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="rounded-card border border-border bg-surface p-4">
      <p className="text-sm font-semibold text-fg-muted">{label}</p>
      <p className="mt-1 font-heading text-3xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 text-sm text-fg-muted">{note}</p>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-card border border-border bg-surface p-4">
      <h2 className="mb-3 font-heading text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Table({ caption, head, rows }: { caption: string; head: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface-muted text-xs text-fg-muted">
          <tr>
            {head.map((h) => (
              <th key={h} scope="col" className="px-3 py-2 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row) => (
            <tr key={row[0]}>
              {row.map((cell, i) => (
                <td key={i} className={`px-3 py-2 ${i > 0 ? "tabular-nums" : ""}`}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
