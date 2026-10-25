// Orders and repairs (US-05, ADR-029, ADR-031): the API's record shapes and the words the chat shows for them.

export type OrderLine = { name: string; sku: string; quantity: number; unit_price_cents: number };

export type Order = {
  kind?: "order";
  order_number: string;
  status: OrderStatus;
  delivery_method: "standard" | "express" | "pickup";
  placed_at: string;
  shipped_at: string | null;
  delivered_at: string | null;
  expected_on: string | null;
  carrier: string | null;
  tracking_number: string | null;
  total_cents: number;
  pickup_store?: string | null;
  pickup_by?: string | null;
  lines: OrderLine[];
};

export type RepairStatus =
  | "received"
  | "diagnosing"
  | "waiting_for_parts"
  | "repairing"
  | "ready_for_pickup"
  | "collected"
  | "cancelled";

export type Repair = {
  kind?: "repair";
  reference: string;
  product_name: string;
  status: RepairStatus;
  reported_fault: string;
  diagnosis: string | null;
  resolution: string | null;
  store: string;
  under_warranty: boolean;
  cost_cents: number | null;
  received_at: string;
  ready_on: string | null;
  collected_at: string | null;
  order_number: string | null;
};

/** What an answer about the customer's own things is about (ADR-031). */
export type AccountRecord = (Order & { kind: "order" }) | (Repair & { kind: "repair" });

export type OrderStatus =
  | "placed"
  | "processing"
  | "shipped"
  | "ready_for_pickup"
  | "delivered"
  | "cancelled"
  | "returned";

// Every status has words, never colour alone (MASTER.md).
export const ORDER_STATUS: Record<OrderStatus, { label: string; tone: "info" | "success" | "warning" | "neutral" }> = {
  placed: { label: "Order placed", tone: "info" },
  processing: { label: "Being prepared", tone: "info" },
  shipped: { label: "On its way", tone: "info" },
  ready_for_pickup: { label: "Ready for pickup", tone: "success" },
  delivered: { label: "Delivered", tone: "success" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  returned: { label: "Returned", tone: "neutral" },
};

export const REPAIR_STATUS: Record<RepairStatus, { label: string; tone: "info" | "success" | "warning" | "neutral" }> =
  {
    received: { label: "Received at the store", tone: "info" },
    diagnosing: { label: "Being diagnosed", tone: "info" },
    waiting_for_parts: { label: "Waiting for parts", tone: "warning" },
    repairing: { label: "Being repaired", tone: "info" },
    ready_for_pickup: { label: "Ready for pickup", tone: "success" },
    collected: { label: "Collected", tone: "neutral" },
    cancelled: { label: "Cancelled", tone: "neutral" },
  };

/** "TC-123456" or "RP-123456" in a question pre-fills the verification card. */
export function recordNumberIn(text: string): string {
  const match = /\b(TC|RP)[-\s]?(\d{6})\b/i.exec(text);
  return match ? `${match[1].toUpperCase()}-${match[2]}` : "";
}

export const DELIVERY: Record<Order["delivery_method"], string> = {
  standard: "Standard delivery",
  express: "Express delivery",
  pickup: "Store pickup",
};

export function money(cents: number): string {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(cents / 100);
}

export function day(iso: string): string {
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(
    new Date(iso.length === 10 ? `${iso}T12:00:00Z` : iso),
  );
}
