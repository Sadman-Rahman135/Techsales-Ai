import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";

// GET: document list (query string passed through). POST: multipart upload, streamed to the API unchanged.
export async function GET(request: NextRequest) {
  return relay(`/api/v1/documents${request.nextUrl.search}`);
}

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const headers: Record<string, string> = { "Content-Type": request.headers.get("content-type") ?? "" };
  const length = request.headers.get("content-length");
  if (length) headers["Content-Length"] = length; // lets the API refuse an oversized upload before reading it
  return relay("/api/v1/documents", { method: "POST", headers, body: request.body, duplex: "half" });
}
