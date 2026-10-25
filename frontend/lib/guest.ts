import "server-only";

import { cookies } from "next/headers";

import { getToken } from "@/lib/session";

// Anonymous chat (ADR-017): the browser holds a random reference in this httpOnly cookie; the BFF sends it to
// the API as X-Session-Ref, and the API stores only its SHA-256. Clearing cookies starts a fresh, empty history.
export const GUEST_COOKIE = "kai_guest";
const GUEST_DAYS = 30;

function newReference(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return Buffer.from(bytes).toString("base64url"); // 43 chars, within the API's [A-Za-z0-9_-]{22,128}
}

/**
 * Headers that identify the caller for chat routes: nothing extra for signed-in users (the bearer token wins),
 * the guest reference otherwise. `create` mints one; only Route Handlers may do that (they can set cookies).
 */
export async function chatIdentity(create: boolean): Promise<Record<string, string> | null> {
  if (await getToken()) return {};
  const store = await cookies();
  let reference = store.get(GUEST_COOKIE)?.value ?? null;
  if (!reference && create) {
    reference = newReference();
    store.set(GUEST_COOKIE, reference, {
      httpOnly: true,
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
      path: "/",
      maxAge: GUEST_DAYS * 24 * 60 * 60,
    });
  }
  return reference ? { "X-Session-Ref": reference } : null;
}
