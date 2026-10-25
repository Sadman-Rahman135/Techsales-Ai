// Server-side access to the FastAPI backend (ADR-005: the browser never calls the API directly).
// API_BASE_URL is deliberately not NEXT_PUBLIC_*, so it is never bundled into client code.
// 127.0.0.1, not localhost: on Windows, localhost tries IPv6 first and adds ~2 s per connection.
export const API_BASE_URL = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

export type ComponentCheck = { name: string; ok: boolean; latency_ms: number; detail: string | null };
export type HealthReport = { status: "ok" | "degraded"; app_env: string; checks: ComponentCheck[] };

export type HealthResult = { reachable: true; report: HealthReport } | { reachable: false; reason: string };

export async function getHealth(): Promise<HealthResult> {
  try {
    // 200 = healthy, 503 = degraded; both carry a HealthReport body.
    const response = await fetch(`${API_BASE_URL}/api/v1/health`, { signal: AbortSignal.timeout(5000) });
    if (response.status !== 200 && response.status !== 503) {
      return { reachable: false, reason: `Unexpected HTTP ${response.status}` };
    }
    return { reachable: true, report: (await response.json()) as HealthReport };
  } catch (error) {
    const timedOut = error instanceof DOMException && error.name === "TimeoutError";
    return { reachable: false, reason: timedOut ? "Timed out after 5 s" : "Connection refused" };
  }
}
