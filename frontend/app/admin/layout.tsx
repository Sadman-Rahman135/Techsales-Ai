// The admin area is never indexed by search engines.
import type { Metadata } from "next";

export const metadata: Metadata = { robots: { index: false, follow: false } };

export default function AdminLayout({ children }: LayoutProps<"/admin">) {
  return children;
}
