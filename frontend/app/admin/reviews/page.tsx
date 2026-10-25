import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { AdminShell } from "@/components/admin/admin-shell";
import { NoPermission } from "@/components/admin/no-permission";
import { ReviewsView, type ReviewQuery } from "@/components/admin/reviews-view";
import { canManageDocuments, requireUser } from "@/lib/auth";
import { backendFetch } from "@/lib/backend";
import type { ReviewPage } from "@/lib/reviews";

export const metadata: Metadata = {
  title: "Review",
  robots: { index: false, follow: false },
};

const PATH = "/admin/reviews";
const STATUSES = new Set(["open", "resolved", "dismissed"]);
const REASONS = new Set(["unanswered", "not_helpful"]);
const LIMIT = 50;

export default function ReviewsPage({ searchParams }: PageProps<"/admin/reviews">) {
  return (
    <AdminShell current={PATH}>
      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 outline-none sm:px-6 lg:py-8">
        <Suspense fallback={<p className="text-fg-muted">Loading the review queue…</p>}>
          <ReviewsSection searchParams={searchParams} />
        </Suspense>
      </main>
    </AdminShell>
  );
}

function first(value: string | string[] | undefined): string {
  return (Array.isArray(value) ? value[0] : value) ?? "";
}

async function ReviewsSection({ searchParams }: { searchParams: PageProps<"/admin/reviews">["searchParams"] }) {
  const me = await requireUser(PATH);
  if (!canManageDocuments(me)) return <NoPermission what="review questions" />;
  const params = await searchParams;
  const query: ReviewQuery = {
    status: STATUSES.has(first(params.status)) ? (first(params.status) as ReviewQuery["status"]) : "open",
    reason: REASONS.has(first(params.reason)) ? (first(params.reason) as ReviewQuery["reason"]) : "",
  };
  const api = new URLSearchParams({
    status: query.status,
    limit: String(LIMIT),
  });
  if (query.reason) api.set("reason", query.reason);
  const [response, documents] = await Promise.all([
    backendFetch(`/api/v1/admin/reviews?${api}`),
    backendFetch("/api/v1/documents?sort=title&limit=100"),
  ]);
  if (response.status === 401) redirect(`/login?next=${encodeURIComponent(PATH)}`);
  if (!response.ok) throw new Error(`review queue failed: HTTP ${response.status}`);
  const page = (await response.json()) as ReviewPage;
  const titles = documents.ok
    ? ((await documents.json()) as { items: { id: string; title: string }[] }).items.map(({ id, title }) => ({
        id,
        title,
      }))
    : [];
  const now = Date.parse(response.headers.get("date") ?? "") || 0;
  return (
    <ReviewsView page={page} query={query} documents={titles} timezone={me.organization.timezone} renderedAt={now} />
  );
}
