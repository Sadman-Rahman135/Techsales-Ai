import { relay } from "@/lib/bff";

// GET: a signed-in customer's own orders (US-05).
export async function GET() {
  return relay("/api/v1/orders/mine");
}
