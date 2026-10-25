import type { NextRequest } from "next/server";

import { crossSiteRefused, relay, sameOrigin } from "@/lib/bff";
import { chatIdentity } from "@/lib/guest";

// GET: the caller's conversations (signed-in users, or this browser's guest session). DELETE: clear history.
export async function GET() {
  const identity = await chatIdentity(false);
  if (!identity) return Response.json([]); // a guest who never asked has no history
  return relay("/api/v1/conversations", { headers: identity });
}

export async function DELETE(request: NextRequest) {
  if (!sameOrigin(request)) return crossSiteRefused();
  const identity = await chatIdentity(false);
  if (!identity) return new Response(null, { status: 204 });
  return relay("/api/v1/conversations", { method: "DELETE", headers: identity });
}
