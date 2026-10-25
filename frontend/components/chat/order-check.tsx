"use client";

import {
  CircleCheck,
  LoaderCircle,
  MessageCircleQuestion,
  Package,
  PackageCheck,
  ShieldCheck,
  Store,
  Truck,
  TriangleAlert,
  Undo2,
  Wrench,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useId, useState, type FormEvent } from "react";

import type { ChatError } from "@/lib/chat";
import {
  type AccountRecord,
  day,
  DELIVERY,
  money,
  type Order,
  ORDER_STATUS,
  type OrderStatus,
  type Repair,
  REPAIR_STATUS,
  type RepairStatus,
} from "@/lib/orders";

// US-05 verification card (content-guide.md "order-status verification") and the order/repair cards (ADR-031).
// Record data comes from the orders and repairs APIs or the answer's `records`, both filled by fixed queries for
// this caller; never from the model's words.

const ICONS: Record<OrderStatus, LucideIcon> = {
  placed: Package,
  processing: Package,
  shipped: Truck,
  ready_for_pickup: Store,
  delivered: PackageCheck,
  cancelled: XCircle,
  returned: Undo2,
};

const REPAIR_ICONS: Record<RepairStatus, LucideIcon> = {
  received: Wrench,
  diagnosing: Wrench,
  waiting_for_parts: Wrench,
  repairing: Wrench,
  ready_for_pickup: Store,
  collected: CircleCheck,
  cancelled: XCircle,
};

const TONES = {
  info: "text-accent",
  success: "text-success",
  warning: "text-warning",
  neutral: "text-fg-muted",
} as const;

const input =
  "mt-1.5 block min-h-11 w-full rounded-control border border-border-strong bg-surface px-3 text-base text-fg placeholder:text-fg-muted/70";

type Checked = { kind: "order"; order: Order } | { kind: "repair"; repair: Repair };

export function OrderCheck({
  signedIn,
  orderNumber = "",
  conversationId = null,
}: {
  signedIn: boolean;
  orderNumber?: string;
  conversationId?: string | null;
}) {
  const id = useId();
  const [checked, setChecked] = useState<Checked | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mine, setMine] = useState<Checked[]>([]);

  useEffect(() => {
    if (!signedIn) return;
    let cancelled = false;
    const get = <T,>(url: string) =>
      fetch(url)
        .then((r) => (r.ok ? (r.json() as Promise<T[]>) : []))
        .catch(() => [] as T[]);
    Promise.all([get<Order>("/api/orders/mine"), get<Repair>("/api/repairs/mine")]).then(([orders, repairs]) => {
      if (cancelled) return;
      setMine([
        ...orders.map((order): Checked => ({ kind: "order", order })),
        ...repairs.map((repair): Checked => ({ kind: "repair", repair })),
      ]);
    });
    return () => {
      cancelled = true;
    };
  }, [signedIn]);

  async function check(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const number = String(form.get("number") ?? "").trim();
    const contact = String(form.get("contact") ?? "").trim();
    const repair = /^rp/i.test(number);
    setBusy(true);
    setError(null);
    const response = await fetch(repair ? "/api/repairs/lookup" : "/api/orders/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        repair
          ? { reference: number, contact, conversation_id: conversationId }
          : { order_number: number, contact, conversation_id: conversationId },
      ),
    }).catch(() => null);
    setBusy(false);
    if (response?.ok) {
      setChecked(
        repair
          ? { kind: "repair", repair: (await response.json()) as Repair }
          : { kind: "order", order: (await response.json()) as Order },
      );
      return;
    }
    const body = response ? ((await response.json().catch(() => null)) as { error?: ChatError } | null) : null;
    if (response?.status === 422) {
      setError(
        "Enter the order number (it starts with TC-) or repair number (RP-), and the email or phone number on it.",
      );
    } else {
      setError(body?.error?.message ?? "That didn't reach us. Check your connection and try again.");
    }
  }

  if (checked) {
    return (
      <div className="space-y-2">
        {checked.kind === "order" ? <OrderCard order={checked.order} /> : <RepairCard repair={checked.repair} />}
        {conversationId ? (
          <p className="flex items-start gap-2 text-sm text-fg-muted">
            <MessageCircleQuestion className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            You can now ask about it here, for example &ldquo;When can I pick it up?&rdquo;
          </p>
        ) : null}
        <button
          type="button"
          onClick={() => setChecked(null)}
          className="inline-flex min-h-11 items-center text-sm font-semibold text-accent hover:underline"
        >
          Check another order or repair
        </button>
      </div>
    );
  }

  return (
    <section aria-labelledby={`${id}-title`} className="rounded-card border border-border bg-surface-muted p-4">
      <h3 id={`${id}-title`} className="flex items-center gap-2 font-semibold">
        <ShieldCheck className="size-5 text-accent" aria-hidden="true" />
        Confirm it&apos;s yours
      </h3>
      <p className="mt-1 text-sm text-fg-muted">
        To protect your details, we need the order or repair number and the email or phone number on it.
      </p>
      {mine.length ? (
        <div className="mt-3">
          <p className="text-sm font-semibold">Your recent orders and repairs</p>
          <ul className="mt-1.5 flex flex-wrap gap-2">
            {mine.map((item) => {
              const number = item.kind === "order" ? item.order.order_number : item.repair.reference;
              const label =
                item.kind === "order" ? ORDER_STATUS[item.order.status].label : REPAIR_STATUS[item.repair.status].label;
              return (
                <li key={number}>
                  <button
                    type="button"
                    onClick={() => setChecked(item)}
                    className="inline-flex min-h-11 items-center gap-2 rounded-control border border-border-strong bg-surface px-3 text-sm font-semibold hover:bg-accent/10"
                  >
                    {item.kind === "repair" ? <Wrench className="size-4 text-fg-muted" aria-hidden="true" /> : null}
                    {number}
                    <span className="font-normal text-fg-muted">· {label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
      <form onSubmit={check} className="mt-3 grid gap-3 sm:grid-cols-2" noValidate>
        <div>
          <label htmlFor={`${id}-number`} className="block text-sm font-semibold">
            Order or repair number
          </label>
          <input
            id={`${id}-number`}
            name="number"
            required
            maxLength={20}
            autoComplete="off"
            defaultValue={orderNumber}
            aria-describedby={`${id}-number-hint`}
            placeholder="TC-123456"
            className={input}
          />
          <p id={`${id}-number-hint`} className="mt-1 text-xs text-fg-muted">
            TC- on your receipt, or RP- on your repair receipt
          </p>
        </div>
        <div>
          <label htmlFor={`${id}-contact`} className="block text-sm font-semibold">
            Email or phone
          </label>
          <input
            id={`${id}-contact`}
            name="contact"
            required
            maxLength={254}
            autoComplete="email"
            className={input}
          />
        </div>
        {error ? (
          <p role="alert" className="flex items-start gap-2 text-sm sm:col-span-2">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
            <span>{error}</span>
          </p>
        ) : null}
        <div className="sm:col-span-2">
          <button
            type="submit"
            disabled={busy}
            className="inline-flex min-h-11 items-center gap-2 rounded-control bg-accent px-4 font-semibold text-on-accent hover:bg-accent/90 disabled:opacity-60"
          >
            {busy ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : null}
            {busy ? "Checking…" : "Check"}
          </button>
        </div>
      </form>
    </section>
  );
}

/** The cards under an answer about the customer's own orders and repairs. */
export function RecordCards({ records }: { records: AccountRecord[] }) {
  if (records.length === 0) return null;
  return (
    <div className="space-y-2">
      {records.map((record) =>
        record.kind === "order" ? (
          <OrderCard key={record.order_number} order={record} />
        ) : (
          <RepairCard key={record.reference} repair={record} />
        ),
      )}
    </div>
  );
}

function Facts({ facts }: { facts: [string, string][] }) {
  return (
    <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
      {facts.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-fg-muted">{term}</dt>
          <dd className="tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Source() {
  return (
    <p className="mt-3 flex items-center gap-2 text-xs text-fg-muted">
      <CircleCheck className="size-4" aria-hidden="true" />
      From TechCare&apos;s records, not from the assistant.
    </p>
  );
}

function OrderCard({ order }: { order: Order }) {
  const status = ORDER_STATUS[order.status];
  const Icon = ICONS[order.status];
  const facts: [string, string][] = [
    ["Ordered", day(order.placed_at)],
    ["Delivery", DELIVERY[order.delivery_method]],
  ];
  if (order.shipped_at) facts.push(["Shipped", day(order.shipped_at)]);
  if (order.delivered_at) facts.push(["Delivered", day(order.delivered_at)]);
  else if (order.expected_on && ["placed", "processing", "shipped"].includes(order.status)) {
    facts.push(["Expected", day(order.expected_on)]);
  }
  if (order.carrier && order.tracking_number) facts.push(["Tracking", `${order.carrier} ${order.tracking_number}`]);
  if (order.pickup_store) facts.push(["Pickup store", order.pickup_store]);
  if (order.pickup_by) facts.push(["Collect by", day(order.pickup_by)]);
  return (
    <section aria-label={`Order ${order.order_number}`} className="rounded-card border border-border bg-surface p-4">
      <p role="status" className={`flex items-center gap-2 font-semibold ${TONES[status.tone]}`}>
        <Icon className="size-5" aria-hidden="true" />
        <span className="text-fg">
          {order.order_number}: {status.label}
        </span>
      </p>
      <Facts facts={facts} />
      <ul className="mt-3 divide-y divide-border border-y border-border text-sm">
        {order.lines.map((line) => (
          <li key={line.sku} className="flex justify-between gap-3 py-2">
            <span>
              {line.quantity} × {line.name}
            </span>
            <span className="tabular-nums">{money(line.unit_price_cents * line.quantity)}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 flex justify-between text-sm font-semibold">
        <span>Total</span>
        <span className="tabular-nums">{money(order.total_cents)}</span>
      </p>
      <Source />
    </section>
  );
}

function RepairCard({ repair }: { repair: Repair }) {
  const status = REPAIR_STATUS[repair.status];
  const Icon = REPAIR_ICONS[repair.status];
  const facts: [string, string][] = [
    ["Product", repair.product_name],
    ["Fault reported", repair.reported_fault],
    ["Diagnosis", repair.diagnosis ?? "Not diagnosed yet"],
  ];
  if (repair.resolution) facts.push(["Work done", repair.resolution]);
  facts.push(["Store", repair.store], ["Received", day(repair.received_at)]);
  if (repair.collected_at) facts.push(["Collected", day(repair.collected_at)]);
  else if (repair.ready_on) facts.push([repair.status === "ready_for_pickup" ? "Ready since" : "Ready by", day(repair.ready_on)]);
  facts.push([
    "Cost",
    repair.under_warranty ? "Covered by warranty" : repair.cost_cents === null ? "Not quoted yet" : money(repair.cost_cents),
  ]);
  return (
    <section aria-label={`Repair ${repair.reference}`} className="rounded-card border border-border bg-surface p-4">
      <p role="status" className={`flex items-center gap-2 font-semibold ${TONES[status.tone]}`}>
        <Icon className="size-5" aria-hidden="true" />
        <span className="text-fg">
          {repair.reference}: {status.label}
        </span>
      </p>
      <Facts facts={facts} />
      <Source />
    </section>
  );
}
