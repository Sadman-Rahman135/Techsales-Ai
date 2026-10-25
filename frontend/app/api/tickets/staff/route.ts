import { relay } from "@/lib/bff";

// GET: who a ticket can be assigned to.
export async function GET() {
  return relay("/api/v1/tickets/staff");
}
