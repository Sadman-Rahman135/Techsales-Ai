import type { NextRequest } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { apiUnavailable } from "@/lib/bff";

// GET: the original file behind a signed link (ADR-018). The link itself is the credential, so no session is
// forwarded. Same-origin framing only (the source panel), never cached, never sent onward as a referrer.
export async function GET(_request: NextRequest, ctx: RouteContext<"/api/files/[token]">) {
  const { token } = await ctx.params;
  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE_URL}/api/v1/files/${encodeURIComponent(token)}`, { cache: "no-store" });
  } catch {
    return apiUnavailable();
  }
  if (!upstream.ok || !upstream.body) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  }
  const type = upstream.headers.get("content-type") ?? "application/octet-stream";
  // `sandbox` stops Chrome's PDF viewer from loading inside the source panel's frame, so PDFs go without it:
  // with a fixed application/pdf type and nosniff they can't run as a page. Everything else stays sandboxed.
  const sandbox = type.startsWith("application/pdf") ? "" : "; sandbox";
  const headers = new Headers({
    "Content-Type": type,
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Content-Security-Policy": `default-src 'none'; frame-ancestors 'self'${sandbox}`,
  });
  const disposition = upstream.headers.get("content-disposition");
  if (disposition) headers.set("Content-Disposition", disposition);
  return new Response(upstream.body, { headers });
}
