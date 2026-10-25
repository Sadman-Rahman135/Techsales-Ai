import { relay } from "@/lib/bff";

// GET: a signed-in customer's own repair jobs (ADR-031).
export async function GET() {
  return relay("/api/v1/repairs/mine");
}
