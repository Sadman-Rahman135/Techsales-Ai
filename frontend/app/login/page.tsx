import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { SiteHeader } from "@/components/site-header";

import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="mx-auto w-full max-w-md flex-1 px-4 py-12 sm:py-16">
        <div className="rounded-card border border-border bg-surface p-6 sm:p-8">
          <h1 className="font-heading text-2xl font-semibold">Sign in</h1>
          <p className="mt-1 text-sm text-fg-muted">
            For TechCare staff and customers with an account.{" "}
            <Link href="/chat" className="font-semibold text-accent underline underline-offset-2">
              Continue as guest
            </Link>{" "}
            to ask the assistant without one.
          </p>
          {/* useSearchParams reads the request, so the form streams in behind a boundary. */}
          <Suspense fallback={<div className="mt-6 h-64" aria-hidden="true" />}>
            <LoginForm />
          </Suspense>
        </div>
      </main>
    </>
  );
}
