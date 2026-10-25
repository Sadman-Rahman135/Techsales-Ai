"use server";

import { redirect } from "next/navigation";

import { API_BASE_URL } from "@/lib/api";
import { homeFor, type Role } from "@/lib/auth";
import { clearSession, setSession } from "@/lib/session";

export type SignInState = { error: string | null; email: string };

// Only same-site paths: never redirect to another origin after sign-in (open-redirect guard). Without a
// `next`, each role lands on its own home (customers: chat; employees: assistant; managers: documents).
function safeNext(value: FormDataEntryValue | null, role: Role): string {
  const next = typeof value === "string" ? value : "";
  return next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : homeFor(role);
}

export async function signIn(_previous: SignInState, formData: FormData): Promise<SignInState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  if (!email || !password) return { error: "Enter your email and password.", email };

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });
  } catch {
    return { error: "We can't reach the sign-in service right now. Try again in a moment.", email };
  }
  if (response.status === 401 || response.status === 422) {
    return { error: "The email or password is incorrect.", email };
  }
  if (response.status === 429) {
    return { error: "Too many failed sign-ins. Wait 15 minutes, then try again.", email };
  }
  if (!response.ok) return { error: "Sign-in failed on our side. Try again in a moment.", email };

  const session = (await response.json()) as { access_token: string; expires_at: string; me: { role: Role } };
  await setSession(session.access_token, session.expires_at);
  redirect(safeNext(formData.get("next"), session.me.role));
}

export async function signOut(): Promise<void> {
  await clearSession();
  redirect("/login");
}
