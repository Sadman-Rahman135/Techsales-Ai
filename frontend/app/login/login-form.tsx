"use client";

import { LoaderCircle, LogIn, TriangleAlert } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useActionState } from "react";

import { signIn, type SignInState } from "./actions";

const initial: SignInState = { error: null, email: "" };

export function LoginForm() {
  const next = useSearchParams().get("next") ?? "";
  const [state, action, pending] = useActionState(signIn, initial);

  return (
    <form action={action} className="mt-6 space-y-5" noValidate>
      <input type="hidden" name="next" value={next} />
      <div>
        <label htmlFor="email" className="block text-sm font-semibold">
          Email
        </label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          required
          defaultValue={state.email}
          aria-describedby={state.error ? "signin-error" : undefined}
          className="mt-1.5 block min-h-11 w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg"
        />
      </div>
      <div>
        <label htmlFor="password" className="block text-sm font-semibold">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          aria-describedby={state.error ? "signin-error" : undefined}
          className="mt-1.5 block min-h-11 w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg"
        />
      </div>
      {state.error ? (
        <p id="signin-error" role="alert" className="flex items-start gap-2 text-sm text-danger">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {state.error}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={pending}
        className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-control bg-accent px-4 font-semibold text-on-accent transition-colors duration-150 hover:bg-accent/90 disabled:opacity-70"
      >
        {pending ? (
          <LoaderCircle className="size-4 animate-spin" aria-hidden="true" />
        ) : (
          <LogIn className="size-4" aria-hidden="true" />
        )}
        {pending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
