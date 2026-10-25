import "server-only";

import { connection } from "next/server";

import { API_BASE_URL } from "@/lib/api";
import { getToken } from "@/lib/session";

// Every BFF call to FastAPI goes through here: it adds the session's bearer token and a request ID, so one
// request can be traced from the browser to the API (blueprint §9, §13).
export async function backendFetch(path: string, init: RequestInit & { duplex?: "half" } = {}): Promise<Response> {
  await connection(); // per-request work (random request ID, live data): never part of a prerender
  const headers = new Headers(init.headers);
  const token = await getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!headers.has("X-Request-ID")) headers.set("X-Request-ID", `req-${crypto.randomUUID().replaceAll("-", "")}`);
  return fetch(`${API_BASE_URL}${path}`, { ...init, headers, cache: "no-store" });
}

export type ApiErrorBody = {
  error: { code: string; message: string; request_id: string; details?: Record<string, unknown> };
};

export async function readError(response: Response): Promise<ApiErrorBody["error"]> {
  try {
    return ((await response.json()) as ApiErrorBody).error;
  } catch {
    return { code: "BAD_GATEWAY", message: "The service didn't respond as expected.", request_id: "unknown" };
  }
}
