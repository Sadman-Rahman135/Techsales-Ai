import { ArrowDown, ArrowRight, ArrowUp, Download, Lock, Minus, SearchX, ThumbsDown, ThumbsUp } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { AdminShell } from "@/components/admin/admin-shell";
import { NoPermission } from "@/components/admin/no-permission";
import { absoluteTime } from "@/components/admin/when";
import { ChartCard, HorizontalBars, OutcomeBars, OutcomeLegend, ShareLine, Sparkline } from "@/components/quality/charts";
import { RangeControl, RetryJob } from "@/components/quality/controls";
import { canManageDocuments, requireUser } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";
import {
  type Analytics,
  type Change,
  change,
  daysBetween,
  INTENT_LABEL,
  percent,
  previousRange,
  resolveRange,
  shortDay,
} from "@/lib/quality";
import { EXPORTS, exportUrl } from "@/lib/reviews";

export const metadata: Metadata = { title: "Quality" };

const PATH = "/admin/quality";

// design-system/pages/quality-dashboard.md (§10.4, US-06). Every number comes from GET /admin/analytics for the
// range in the URL, so the API test "counts match the activity" covers what is shown here.
export default function QualityPage({ searchParams }: PageProps<"/admin/quality">) {
  return (
    <AdminShell current={PATH}>
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 outline-none sm:px-6 lg:py-8">
        <h1 className="font-heading text-2xl font-semibold">Quality</h1>
        <p className="mt-1 text-sm text-fg-muted">
          How the assistant is doing: what it answered, what it couldn&apos;t, and what people thought of it.
        </p>
        <Suspense fallback={<DashboardSkeleton />}>
          <Dashboard searchParams={searchParams} />
        </Suspense>
      </main>
    </AdminShell>
  );
}

function one(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

async function fetchAnalytics(start: string, end: string): Promise<Response> {
  return backendFetch(`/api/v1/admin/analytics?start=${start}&end=${end}`);
}

async function Dashboard({ searchParams }: { searchParams: PageProps<"/admin/quality">["searchParams"] }) {
  const me = await requireUser(PATH);
  if (!canManageDocuments(me)) return <NoPermission what="see quality reports" />;
  const params = await searchParams;
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: me.organization.timezone }).format(new Date());
  const range = resolveRange({ range: one(params.range), start: one(params.start), end: one(params.end) }, today);
  const previous = previousRange(range);
  const [response, previousResponse] = await Promise.all([
    fetchAnalytics(range.start, range.end),
    fetchAnalytics(previous.start, previous.end),
  ]);
  if (response.status === 401) redirect(`/login?next=${PATH}`);
  if (response.status === 422) {
    const body = (await response.json()) as { error: { message: string } };
    return (
      <div className="mt-6 space-y-4">
        <RangeControl range={range} label="" />
        <p role="alert" className="rounded-card border border-border bg-surface p-4">
          {body.error.message}
        </p>
      </div>
    );
  }
  if (!response.ok) throw new Error(`analytics failed: HTTP ${response.status}`);
  const data = (await response.json()) as Analytics;
  const before = previousResponse.ok ? ((await previousResponse.json()) as Analytics) : null;
  const days = daysBetween(range.start, range.end);
  const vs = `vs the previous ${days === 1 ? "day" : `${days} days`}`;
  const t = data.totals;
  const p = before?.totals ?? null;
  const answeredPct = percent(t.answered, t.questions);
  const missingPct = percent(t.insufficient_evidence, t.questions);
  const empty = t.questions === 0;

  return (
    <div className="mt-6 space-y-6">
      <RangeControl range={range} label={data.range.label} />

      <section aria-label="Key figures" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi
          label="Questions"
          value={String(t.questions)}
          change={change(t.questions, p?.questions ?? null, "count")}
          vs={vs}
          series={data.daily.map((d) => d.answered + d.insufficient_evidence + d.direct + d.error + d.escalated)}
        />
        <Kpi
          label="Answered"
          value={answeredPct === null ? "No data" : `${answeredPct}%`}
          change={change(answeredPct, p ? percent(p.answered, p.questions) : null, "points")}
          vs={vs}
          series={data.daily.map((d) => percent(d.answered, total(d)) ?? 0)}
        />
        <Kpi
          label="Not in documents"
          value={missingPct === null ? "No data" : `${missingPct}%`}
          change={change(missingPct, p ? percent(p.insufficient_evidence, p.questions) : null, "points")}
          vs={vs}
          series={data.daily.map((d) => percent(d.insufficient_evidence, total(d)) ?? 0)}
        />
        <Kpi
          label="Failed calls"
          value={String(t.error)}
          change={change(t.error, p?.error ?? null, "count")}
          vs={vs}
          series={data.daily.map((d) => d.error)}
        />
      </section>
      <p className="text-sm text-fg-muted">
        Answer time: median {data.latency_ms.p50 ?? "–"} ms, 95th percentile {data.latency_ms.p95 ?? "–"} ms.
        Greetings and hand-offs ({t.direct}) count as questions but aren&apos;t answered from documents.
      </p>

      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <ChartCard
          title="Answered vs not in documents"
          summary={
            empty
              ? "No questions in this period."
              : `${t.answered} answered from sources and ${t.insufficient_evidence} not in our documents, by day.`
          }
          chart={empty ? <Empty /> : <OutcomeBars daily={data.daily} />}
          legend={empty ? null : <OutcomeLegend />}
          table={
            <Table
              caption="Questions by day"
              head={["Day", "Answered", "Not in documents", "Failed", "Greetings and hand-offs"]}
              rows={data.daily.map((d) => [shortDay(d.date), d.answered, d.insufficient_evidence, d.error, d.direct])}
            />
          }
        />
        <ChartCard
          title="Top intents"
          summary={
            data.intents.length
              ? `Most questions were ${(INTENT_LABEL[data.intents[0].intent] ?? data.intents[0].intent).toLowerCase()} (${data.intents[0].count}).`
              : "No questions in this period."
          }
          chart={
            data.intents.length ? (
              <HorizontalBars
                items={data.intents.map((i) => ({ label: INTENT_LABEL[i.intent] ?? i.intent, value: i.count }))}
              />
            ) : (
              <Empty />
            )
          }
          table={
            <Table
              caption="Questions by intent"
              head={["Intent", "Questions"]}
              rows={data.intents.map((i) => [INTENT_LABEL[i.intent] ?? i.intent, i.count])}
            />
          }
        />
      </div>

      <section aria-labelledby="unanswered-title" className="rounded-card border border-border bg-surface p-4">
        <h2 id="unanswered-title" className="flex items-center gap-2 font-heading text-base font-semibold">
          <SearchX className="size-4 text-warning" aria-hidden="true" />
          Unanswered questions
        </h2>
        <p className="mt-1 text-sm text-fg-muted">
          Questions the documents didn&apos;t cover. Personal details are removed before they reach this page. Add or
          update a document to answer the common ones.
        </p>
        <ReviewLink href="/admin/reviews?reason=unanswered" label="Review unanswered questions" />
        {data.unanswered.length === 0 ? (
          <p className="mt-3 text-sm text-fg-muted">Nothing went unanswered in this period.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-sm">
              <caption className="sr-only">Unanswered questions, newest first</caption>
              <thead className="bg-surface-muted text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-3 py-2 font-semibold">Question</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Asked</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Conversation</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.unanswered.slice(0, 15).map((row) => (
                  <tr key={row.answer_id} className="align-top">
                    <td className="max-w-[60ch] px-3 py-2">{row.question}</td>
                    <td className="px-3 py-2 whitespace-nowrap text-fg-muted">
                      {absoluteTime(row.asked_at, data.range.timezone)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <span className="inline-flex items-center gap-1 text-fg-muted">
                        <Lock className="size-4" aria-hidden="true" />
                        Restricted
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.unanswered.length > 15 ? (
              <p className="mt-2 text-sm text-fg-muted">Showing the newest 15 of {data.unanswered.length}.</p>
            ) : null}
          </div>
        )}
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Common missing topics">
          {data.missing_topics.length ? (
            <ul className="flex flex-wrap gap-2" aria-label="Words in unanswered questions">
              {data.missing_topics.map((m) => (
                <li key={m.term} className="rounded-full bg-surface-muted px-3 py-1 text-sm">
                  {m.term} <span className="text-fg-muted tabular-nums">× {m.count}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-fg-muted">None in this period.</p>
          )}
        </Panel>
        <Panel title="Most-cited documents">
          {data.most_cited.length ? (
            <ol className="space-y-2">
              {data.most_cited.map((doc) => (
                <li key={doc.document_id} className="flex justify-between gap-3 text-sm">
                  <Link href={`/admin/documents/${doc.document_id}`} className="text-accent underline-offset-2 hover:underline">
                    {doc.title}
                  </Link>
                  <span className="shrink-0 tabular-nums text-fg-muted">{doc.citations} citations</span>
                </li>
              ))}
            </ol>
          ) : (
            <p className="text-sm text-fg-muted">No citations in this period.</p>
          )}
        </Panel>
        <Panel title="Feedback">
          <p className="flex flex-wrap gap-4 text-sm">
            <span className="inline-flex items-center gap-1.5">
              <ThumbsUp className="size-4 text-success" aria-hidden="true" />
              {data.feedback.helpful} helpful
            </span>
            <span className="inline-flex items-center gap-1.5">
              <ThumbsDown className="size-4 text-danger" aria-hidden="true" />
              {data.feedback.not_helpful} not helpful
            </span>
          </p>
          {data.feedback.recent_not_helpful.length ? (
            <>
              <h3 className="mt-3 text-sm font-semibold">Corrections to review</h3>
              <ul className="mt-2 space-y-3">
                {data.feedback.recent_not_helpful.slice(0, 5).map((f) => (
                  <li key={f.answer_id + f.created_at} className="text-sm">
                    <p>&ldquo;{f.question}&rdquo;</p>
                    <p className="text-fg-muted">
                      {[f.reasons.join(", "), f.note ? `“${f.note}”` : null].filter(Boolean).join(" · ") || "No reason given"}
                    </p>
                  </li>
                ))}
              </ul>
              <ReviewLink href="/admin/reviews?reason=not_helpful" label="Review all not-helpful answers" />
            </>
          ) : null}
        </Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <FeedbackTrend days={data.feedback_daily} />
        <ExportPanel start={range.start} end={range.end} />
      </div>

      <section aria-labelledby="failures-title" className="rounded-card border border-border bg-surface p-4">
        <h2 id="failures-title" className="font-heading text-base font-semibold">
          Ingestion failures
        </h2>
        {data.ingestion_failures.length === 0 ? (
          <p className="mt-2 text-sm text-fg-muted">No documents failed to process in this period.</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-sm">
              <caption className="sr-only">Documents that failed to process</caption>
              <thead className="bg-surface-muted text-xs text-fg-muted">
                <tr>
                  <th scope="col" className="px-3 py-2 font-semibold">Document</th>
                  <th scope="col" className="px-3 py-2 font-semibold">What happened</th>
                  <th scope="col" className="px-3 py-2 font-semibold">When</th>
                  <th scope="col" className="px-3 py-2"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.ingestion_failures.map((f) => (
                  <tr key={f.job_id} className="align-top">
                    <td className="px-3 py-2">
                      <Link href={`/admin/documents/${f.document_id}`} className="font-semibold text-accent underline-offset-2 hover:underline">
                        {f.title}
                      </Link>{" "}
                      <span className="text-fg-muted">v{f.version_number}</span>
                    </td>
                    <td className="max-w-[48ch] px-3 py-2">
                      {f.error_message ?? f.error_code}
                      {f.failed_stage ? <span className="text-fg-muted"> (at {f.failed_stage})</span> : null}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap text-fg-muted">
                      {absoluteTime(f.finished_at, data.range.timezone)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <RetryJob jobId={f.job_id} title={f.title} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function FeedbackTrend({ days }: { days: Analytics["feedback_daily"] }) {
  const share = days.map((d) => ({ date: d.date, value: percent(d.helpful, d.helpful + d.not_helpful) }));
  const rated = days.reduce((sum, d) => sum + d.helpful + d.not_helpful, 0);
  const helpful = days.reduce((sum, d) => sum + d.helpful, 0);
  const summary = rated
    ? `${helpful} of ${rated} rated answers were helpful (${percent(helpful, rated)}%). Days without ratings are left blank.`
    : "No answers were rated in this period.";
  return (
    <ChartCard
      title="Helpful answers by day"
      summary={summary}
      chart={rated ? <ShareLine points={share} /> : <p className="py-10 text-center text-sm text-fg-muted">{summary}</p>}
      table={
        <Table
          caption="Feedback by day"
          head={["Day", "Helpful", "Not helpful", "Helpful share"]}
          rows={days.map((d, i) => [
            shortDay(d.date),
            d.helpful,
            d.not_helpful,
            share[i].value === null ? "–" : `${share[i].value}%`,
          ])}
        />
      }
    />
  );
}

function ExportPanel({ start, end }: { start: string; end: string }) {
  return (
    <Panel title="Export">
      <p className="text-sm text-fg-muted">
        CSV files for this period. Personal details are removed, the same as on this page, and every download is
        recorded.
      </p>
      <ul className="mt-3 divide-y divide-border">
        {EXPORTS.map((e) => (
          <li key={e.dataset}>
            <a
              href={exportUrl(e.dataset, start, end)}
              download
              className="flex min-h-11 items-center justify-between gap-3 py-2 text-sm hover:text-accent"
            >
              <span>
                <span className="font-semibold">{e.label}</span>
                <span className="block text-xs text-fg-muted">{e.hint}</span>
              </span>
              <Download className="size-4 shrink-0 text-accent" aria-hidden="true" />
            </a>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function ReviewLink({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      className="mt-3 inline-flex min-h-11 items-center gap-1.5 text-sm font-semibold text-accent underline-offset-2 hover:underline"
    >
      {label}
      <ArrowRight className="size-4" aria-hidden="true" />
    </Link>
  );
}

function total(d: Analytics["daily"][number]): number {
  return d.answered + d.insufficient_evidence + d.direct + d.error + d.escalated;
}

function Kpi({
  label,
  value,
  change: delta,
  vs,
  series,
}: {
  label: string;
  value: string;
  change: Change;
  vs: string;
  series: number[];
}) {
  const Icon = !delta ? null : delta.direction === "up" ? ArrowUp : delta.direction === "down" ? ArrowDown : Minus;
  return (
    <div className="rounded-card border border-border bg-surface p-4">
      <p className="text-sm font-semibold text-fg-muted">{label}</p>
      <div className="mt-1 flex items-end justify-between gap-3">
        <p className="font-heading text-3xl font-semibold tabular-nums">{value}</p>
        <Sparkline values={series} label={`${label} by day`} />
      </div>
      <p className="mt-1 flex items-center gap-1 text-sm text-fg-muted">
        {Icon ? <Icon className="size-4" aria-hidden="true" /> : null}
        {delta ? `${delta.text} ${vs}` : `No earlier data to compare`}
      </p>
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

function Empty() {
  return <p className="py-10 text-center text-sm text-fg-muted">No questions in this period.</p>;
}

function Table({ caption, head, rows }: { caption: string; head: string[]; rows: (string | number)[][] }) {
  return (
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
          <tr key={String(row[0])}>
            {row.map((cell, i) => (
              <td key={i} className={`px-3 py-2 ${typeof cell === "number" ? "tabular-nums" : ""}`}>
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DashboardSkeleton() {
  return (
    <div aria-hidden="true" className="mt-6 space-y-4">
      <div className="h-11 w-80 animate-pulse rounded-control bg-surface-muted" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-28 animate-pulse rounded-card bg-surface-muted" />
        ))}
      </div>
      <div className="h-72 animate-pulse rounded-card bg-surface-muted" />
    </div>
  );
}
