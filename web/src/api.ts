// Thin client over the Upshift REST + SSE API. All payloads are untyped JSON from the agents.

export type UpshiftEvent = {
  run_id: string;
  seq: number;
  ts: string;
  type: string;
  agent: string;
  node_id: string | null;
  payload: Record<string, any>;
};

export type Run = {
  id: string;
  repo_url: string;
  policy: string;
  budget_cap_usd: number;
  packages: string[];
  target_project: string | null;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  created_at: string;
  updated_at: string;
  spent_usd: number;
  summary: Record<string, any>;
};

export type Health = {
  status: string;
  version: string;
  nebius_configured: boolean;
  tavily_configured: boolean;
  gitlab_configured: boolean;
  capabilities_probed: boolean;
  executor: string;
  pipeline: "agents" | "noop";
};

export type ModelInfo = {
  key: string;
  id: string;
  tier: number;
  context_tokens: number;
  input_price_per_m: number;
  output_price_per_m: number;
  capabilities: string[];
  available: boolean;
  notes: string;
};

async function json<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${text.slice(0, 300)}`);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => json<Health>("/api/health"),
  models: () => json<{ probed: boolean; models: ModelInfo[] }>("/api/models"),
  runs: () => json<{ runs: Run[] }>("/api/runs"),
  run: (id: string) => json<Run>(`/api/runs/${id}`),
  events: (id: string, after = 0) =>
    json<{ events: UpshiftEvent[] }>(`/api/runs/${id}/events?after=${after}`),
  createRun: (body: {
    repo_url: string;
    policy: string;
    budget_cap_usd?: number;
    packages: string[];
    target_project?: string | null;
  }) => json<Run>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
};

/**
 * Subscribe to a run's event stream. Replays stored events first, then live ones; the
 * server closes the stream after the terminal event. Uses fetch rather than EventSource so
 * named SSE events are handled uniformly and Last-Event-ID resume is explicit.
 */
export async function streamEvents(
  runId: string,
  after: number,
  onEvent: (event: UpshiftEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/runs/${runId}/stream?after=${after}`, {
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (!response.ok || !response.body) throw new Error(`stream failed: ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const dataLines = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart());
      if (dataLines.length) {
        try {
          onEvent(JSON.parse(dataLines.join("\n")) as UpshiftEvent);
        } catch {
          // keepalive comments and malformed frames are ignored
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

export function formatUsd(value: number | undefined | null, digits = 4): string {
  return `$${(value ?? 0).toFixed(digits)}`;
}

export function shortModel(id: string | undefined | null): string {
  if (!id) return "";
  const name = id.split("/").pop() ?? id;
  if (/lightning/i.test(name)) return "Lightning";
  if (/ultra/i.test(name)) return "Ultra";
  if (/super/i.test(name)) return "Super";
  if (/nano/i.test(name)) return "Nano";
  return name;
}
