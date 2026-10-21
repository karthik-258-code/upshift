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

/**
 * Static replay mode. When the API is not reachable (GitLab Pages, or any static host),
 * runs are read from `runs/index.json` and `runs/<id>.json` written by `upshift export`.
 * Detected once per page load; relative paths keep it working under a Pages subpath.
 */
let staticMode: Promise<boolean> | null = null;

export function isStatic(): Promise<boolean> {
  if (staticMode === null) {
    staticMode = fetch("api/health", { headers: { Accept: "application/json" } })
      .then((r) => !(r.ok && (r.headers.get("content-type") ?? "").includes("json")))
      .catch(() => true);
  }
  return staticMode;
}

const STATIC_HEALTH: Health = {
  status: "static",
  version: "replay",
  nebius_configured: false,
  tavily_configured: false,
  gitlab_configured: false,
  capabilities_probed: true,
  executor: "recorded",
  pipeline: "noop",
};

type StaticRun = { run: Run; events: UpshiftEvent[] };
const staticCache = new Map<string, Promise<StaticRun>>();

function staticRun(id: string): Promise<StaticRun> {
  if (!staticCache.has(id)) staticCache.set(id, json<StaticRun>(`runs/${id}.json`));
  return staticCache.get(id)!;
}

export const api = {
  health: async () => ((await isStatic()) ? STATIC_HEALTH : json<Health>("api/health")),
  models: () => json<{ probed: boolean; models: ModelInfo[] }>("api/models"),
  runs: async () =>
    (await isStatic()) ? json<{ runs: Run[] }>("runs/index.json") : json<{ runs: Run[] }>("api/runs"),
  run: async (id: string) => ((await isStatic()) ? (await staticRun(id)).run : json<Run>(`api/runs/${id}`)),
  events: async (id: string, after = 0) =>
    (await isStatic())
      ? { events: (await staticRun(id)).events.filter((e) => e.seq > after) }
      : json<{ events: UpshiftEvent[] }>(`api/runs/${id}/events?after=${after}`),
  createRun: async (body: {
    repo_url: string;
    policy: string;
    budget_cap_usd?: number;
    packages: string[];
    target_project?: string | null;
  }) => {
    if (await isStatic()) throw new Error("This is a recorded replay; run Upshift locally to start new runs.");
    return json<Run>("api/runs", { method: "POST", body: JSON.stringify(body) });
  },
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
  if (await isStatic()) {
    const { events } = await staticRun(runId);
    for (const event of events) {
      if (signal.aborted) return;
      if (event.seq > after) onEvent(event);
    }
    return;
  }
  const response = await fetch(`api/runs/${runId}/stream?after=${after}`, {
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
