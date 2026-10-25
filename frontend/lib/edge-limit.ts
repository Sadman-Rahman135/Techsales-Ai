import "server-only";

// Per-IP cap on anonymous questions (§11.2 "limit queries by … IP"). The BFF is the only public entry point
// (ADR-005), so it is the one place that sees the visitor's address; the API adds per-session and
// all-anonymous caps behind it. In memory, per BFF instance: a shared store is the P1 upgrade.
// Behind a proxy, X-Forwarded-For must be set by that proxy (overwritten, not appended), or it can be forged.
const WINDOW_MS = 60_000;
const hits = new Map<string, number[]>();

export const GUEST_ASKS_PER_IP_PER_MINUTE = Number(process.env.GUEST_ASKS_PER_IP_PER_MINUTE ?? 20);

export function clientIp(headers: Headers): string {
  return headers.get("x-forwarded-for")?.split(",")[0]?.trim() || headers.get("x-real-ip") || "local";
}

/** Records a hit; returns seconds to wait when over `limit`, otherwise null. */
export function overLimit(key: string, limit: number, now = Date.now()): number | null {
  const recent = (hits.get(key) ?? []).filter((t) => now - t < WINDOW_MS);
  if (recent.length >= limit) {
    hits.set(key, recent);
    return Math.max(1, Math.ceil((WINDOW_MS - (now - recent[0])) / 1000));
  }
  recent.push(now);
  hits.set(key, recent);
  if (hits.size > 10_000) {
    for (const [k, times] of hits) if (times.every((t) => now - t >= WINDOW_MS)) hits.delete(k);
  }
  return null;
}
