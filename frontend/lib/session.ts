import "server-only";

import { cookies } from "next/headers";

// ADR-005: the API token lives only in this httpOnly cookie. Client code never sees it; the BFF (Server
// Components, Server Actions and Route Handlers) attaches it to backend calls.
export const SESSION_COOKIE = "kai_session";

export async function getToken(): Promise<string | null> {
  return (await cookies()).get(SESSION_COOKIE)?.value ?? null;
}

export async function setSession(token: string, expiresAt: string): Promise<void> {
  const maxAge = Math.max(0, Math.floor((Date.parse(expiresAt) - Date.now()) / 1000));
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge,
  });
}

export async function clearSession(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
}
