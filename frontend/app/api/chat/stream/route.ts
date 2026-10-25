import type { NextRequest } from "next/server";

import { backendFetch } from "@/lib/backend";
import { apiUnavailable, crossSiteRefused, sameOrigin } from "@/lib/bff";
import { clientIp, GUEST_ASKS_PER_IP_PER_MINUTE, overLimit } from "@/lib/edge-limit";
import { chatIdentity } from "@/lib/guest";

// POST: ask a question and stream the answer (SSE, docs/architecture.md §4). The API's event stream is passed
// through unchanged; refusals (401/404/422/429) arrive before streaming as JSON, also passed through.
export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return crossSiteRefused();
  let body: {
    question?: unknown;
    conversation_id?: unknown;
    product_model?: unknown;
    brand?: unknown;
    category?: unknown;
    file_type?: unknown;
  };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return Response.json(
      { error: { code: "MALFORMED_REQUEST", message: "The request body isn't valid JSON.", request_id: "bff" } },
      { status: 400 },
    );
  }
  const identity = await chatIdentity(true);
  if (identity && "X-Session-Ref" in identity) {
    const wait = overLimit(`ask:${clientIp(request.headers)}`, GUEST_ASKS_PER_IP_PER_MINUTE);
    if (wait !== null) {
      return Response.json(
        {
          error: {
            code: "RATE_LIMITED",
            message: `You've asked a lot of questions quickly. You can ask again in ${wait} seconds.`,
            request_id: "bff",
            details: { retry_after_seconds: wait },
          },
        },
        { status: 429, headers: { "Retry-After": String(wait) } },
      );
    }
  }
  const payload = JSON.stringify({
    question: body.question,
    conversation_id: body.conversation_id ?? null,
    product_model: body.product_model ?? null,
    brand: body.brand ?? null,
    category: body.category ?? null,
    file_type: body.file_type ?? null,
  });
  const init = {
    method: "POST",
    headers: { ...identity, "Content-Type": "application/json", Accept: "text/event-stream" },
    body: payload,
  };
  let upstream: Response;
  try {
    upstream = await backendFetch("/api/v1/chat/ask/stream", { ...init, signal: request.signal });
  } catch {
    return apiUnavailable();
  }
  if (!upstream.ok || !upstream.body) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") ?? "application/json",
        ...(upstream.headers.get("retry-after") ? { "Retry-After": upstream.headers.get("retry-after")! } : {}),
      },
    });
  }
  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no", // proxies must not buffer the stream
    },
  });
}
