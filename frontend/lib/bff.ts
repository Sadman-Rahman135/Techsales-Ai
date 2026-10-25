import "server-only";

import type { NextRequest } from "next/server";

import { backendFetch } from "@/lib/backend";

// Route Handlers don't get the Server Action Origin check, and the session cookie is sent automatically, so
// every state-changing BFF route refuses cross-site requests itself (CSRF).
export function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export function crossSiteRefused(): Response {
  return Response.json(
    { error: { code: "FORBIDDEN", message: "Cross-site request refused.", request_id: "bff" } },
    { status: 403 },
  );
}

// Pass the API's status and JSON body straight through; the API already uses the shared error shape (§9.3).
export async function relay(path: string, init: RequestInit & { duplex?: "half" } = {}): Promise<Response> {
  try {
    const upstream = await backendFetch(path, init);
    if (upstream.status === 204) return new Response(null, { status: 204 });
    const body = await upstream.text();
    const headers: Record<string, string> = {
      "Content-Type": upstream.headers.get("content-type") ?? "application/json",
    };
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) headers["Retry-After"] = retryAfter; // 429s keep their wait
    return new Response(body, { status: upstream.status, headers });
  } catch {
    return apiUnavailable();
  }
}

export function apiUnavailable(): Response {
  return Response.json(
    { error: { code: "API_UNAVAILABLE", message: "The service is unavailable. Try again shortly.", request_id: "bff" } },
    { status: 502 },
  );
}
