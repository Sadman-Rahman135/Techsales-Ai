import "server-only";

import { redirect } from "next/navigation";
import { cache } from "react";

import { backendFetch } from "@/lib/backend";
import { getToken } from "@/lib/session";

export type Role = "admin" | "manager" | "employee" | "customer";

export type Me = {
  user_id: string;
  email: string;
  display_name: string | null;
  role: Role;
  organization: { id: string; name: string; slug: string; timezone: string };
};

// Data Access Layer: the one place that reads the session and asks the API who the user is. The API re-checks
// the membership on every call, so a removed or demoted user loses access immediately. `cache`: one /me call
// per request, however many components ask.
export const getCurrentUser = cache(async (): Promise<Me | null> => {
  if (!(await getToken())) return null;
  const response = await backendFetch("/api/v1/me");
  if (!response.ok) return null;
  return (await response.json()) as Me;
});

export async function requireUser(next: string): Promise<Me> {
  const me = await getCurrentUser();
  if (!me) redirect(`/login?next=${encodeURIComponent(next)}`);
  return me;
}

export function canManageDocuments(me: Me): boolean {
  return me.role === "admin" || me.role === "manager";
}

export function isStaff(me: Me): boolean {
  return me.role !== "customer";
}

/** Where each role lands after sign-in (product-vision.md personas). */
export function homeFor(role: Role): string {
  if (role === "customer") return "/chat";
  if (role === "employee") return "/assistant";
  return "/admin/documents";
}
