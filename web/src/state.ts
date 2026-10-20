// Pure reducer: a list of events (plus the run record) -> everything the UI renders.
// Deterministic, so replaying the first N events reconstructs the exact past state.

import type { Run, UpshiftEvent } from "./api";

export type DepStatus =
  | "discovered"
  | "skipped"
  | "queued"
  | "researching"
  | "patching"
  | "verifying"
  | "reviewing"
  | "done"
  | "failed";

export type Attempt = {
  number: number;
  model: string;
  analysis: string;
  edits: any[];
  rejected: any[];
  diff: string;
  tests?: any;
  kind?: string;
};

export type DepState = {
  name: string;
  current: string | null;
  latest: string | null;
  bump: string;
  risk: number;
  skipReason: string | null;
  usage: { files: string[]; symbols: Record<string, number> };
  status: DepStatus;
  sources: any[];
  searches: any[];
  dossier: any | null;
  attempts: Record<number, Attempt>;
  tests: any | null;
  afterBump: any | null;
  review: any | null;
  mergeRequest: any | null;
  success: boolean | null;
  error: string | null;
  outcome: any | null;
  lastMessage: string;
};

export type RunState = {
  status: string;
  repoUrl: string;
  policy: string;
  budgetCap: number;
  started: string | null;
  finished: string | null;
  deps: Record<string, DepState>;
  order: string[];
  candidates: string[];
  events: UpshiftEvent[];
  costByModel: Record<string, { cost: number; calls: number; tokensIn: number; tokensOut: number }>;
  costByAgent: Record<string, number>;
  totalCost: number;
  budgetWarnings: number[];
  summary: Record<string, any> | null;
  activeAgents: Record<string, string>; // agent -> last message/activity
};

function emptyDep(name: string): DepState {
  return {
    name,
    current: null,
    latest: null,
    bump: "unknown",
    risk: 0,
    skipReason: null,
    usage: { files: [], symbols: {} },
    status: "discovered",
    sources: [],
    searches: [],
    dossier: null,
    attempts: {},
    tests: null,
    afterBump: null,
    review: null,
    mergeRequest: null,
    success: null,
    error: null,
    outcome: null,
    lastMessage: "",
  };
}

export function emptyState(run?: Run | null): RunState {
  return {
    status: run?.status ?? "queued",
    repoUrl: run?.repo_url ?? "",
    policy: run?.policy ?? "balanced",
    budgetCap: run?.budget_cap_usd ?? 0,
    started: null,
    finished: null,
    deps: {},
    order: [],
    candidates: [],
    events: [],
    costByModel: {},
    costByAgent: {},
    totalCost: 0,
    budgetWarnings: [],
    summary: null,
    activeAgents: {},
  };
}

function depOf(state: RunState, nodeId: string | null): DepState | null {
  if (!nodeId || !nodeId.startsWith("dep:")) return null;
  const name = nodeId.slice(4);
  if (!state.deps[name]) {
    state.deps[name] = emptyDep(name);
    state.order.push(name);
  }
  return state.deps[name];
}

function attemptOf(dep: DepState, number: number): Attempt {
  if (!dep.attempts[number]) {
    dep.attempts[number] = { number, model: "", analysis: "", edits: [], rejected: [], diff: "" };
  }
  return dep.attempts[number];
}

export function reduce(events: UpshiftEvent[], run?: Run | null): RunState {
  const state = emptyState(run);
  state.events = events;

  for (const event of events) {
    const p = event.payload ?? {};
    const dep = depOf(state, event.node_id);
    if (event.agent) state.activeAgents[event.agent] = event.type;

    switch (event.type) {
      case "run.started":
        state.status = "running";
        state.started = event.ts;
        state.repoUrl = p.repo_url ?? state.repoUrl;
        state.policy = p.policy ?? state.policy;
        state.budgetCap = p.budget_cap_usd ?? state.budgetCap;
        break;
      case "run.finished":
        state.status = p.status ?? "succeeded";
        state.finished = event.ts;
        state.summary = p;
        break;
      case "run.failed":
        state.status = "failed";
        state.finished = event.ts;
        state.summary = p;
        break;
      case "dep.discovered": {
        if (!dep) break;
        dep.current = p.current ?? null;
        dep.latest = p.latest ?? null;
        dep.bump = p.bump ?? "unknown";
        dep.risk = p.risk ?? 0;
        dep.skipReason = p.skip_reason ?? null;
        dep.usage = p.usage ?? dep.usage;
        dep.dossier = dep.dossier;
        dep.status = dep.skipReason ? "skipped" : "discovered";
        break;
      }
      case "agent.message": {
        if (Array.isArray(p.candidates)) {
          state.candidates = p.candidates;
          for (const name of state.order) {
            const d = state.deps[name];
            if (state.candidates.includes(name)) d.status = "queued";
            else if (d.status === "discovered") d.status = "skipped";
          }
        }
        if (dep && p.message) dep.lastMessage = p.message;
        if (dep && p.title && p.description) dep.mergeRequest = { ...(dep.mergeRequest ?? {}), ...p };
        break;
      }
      case "tool.call":
        if (dep) {
          dep.searches.push(p);
          if (event.agent === "historian") dep.status = "researching";
        }
        break;
      case "evidence.fetched":
        if (dep) {
          dep.sources.push(p);
          dep.status = "researching";
        }
        break;
      case "dossier.ready":
        if (dep) dep.dossier = p;
        break;
      case "patch.proposed": {
        if (!dep) break;
        dep.status = "patching";
        const n = Number(p.attempt ?? 0);
        const attempt = attemptOf(dep, n);
        attempt.model = p.model ?? attempt.model;
        attempt.analysis = p.analysis ?? attempt.analysis;
        attempt.kind = p.kind ?? attempt.kind;
        if (Array.isArray(p.edits)) attempt.edits = p.edits;
        if (Array.isArray(p.changes)) attempt.edits = p.changes.map((c: string) => ({ path: "manifest", justification: c, status: "applied" }));
        if (typeof p.diff === "string") attempt.diff = p.diff;
        break;
      }
      case "patch.rejected":
        if (dep) attemptOf(dep, Number(p.attempt ?? 0)).rejected.push(p);
        break;
      case "sandbox.started":
        if (dep) dep.status = "verifying";
        break;
      case "test.result": {
        if (!dep) break;
        dep.tests = p;
        const label: string = p.label ?? "";
        if (/after version bump/.test(label)) dep.afterBump = p;
        const match = /attempt (\d+)/.exec(label);
        if (match) attemptOf(dep, Number(match[1])).tests = p;
        dep.status = p.ok ? "reviewing" : "patching";
        break;
      }
      case "review.verdict":
        if (dep) {
          dep.review = p;
          dep.status = "reviewing";
        }
        break;
      case "mr.opened":
        if (dep) {
          dep.mergeRequest = { ...(dep.mergeRequest ?? {}), ...p };
          dep.status = "done";
          dep.success = true;
        }
        break;
      case "llm.call": {
        const model = p.model_id ?? "unknown";
        const entry = state.costByModel[model] ?? { cost: 0, calls: 0, tokensIn: 0, tokensOut: 0 };
        entry.cost += p.cost_usd ?? 0;
        entry.calls += 1;
        entry.tokensIn += p.prompt_tokens ?? 0;
        entry.tokensOut += p.completion_tokens ?? 0;
        state.costByModel[model] = entry;
        state.costByAgent[event.agent] = (state.costByAgent[event.agent] ?? 0) + (p.cost_usd ?? 0);
        state.totalCost += p.cost_usd ?? 0;
        break;
      }
      case "budget.warning":
        state.budgetWarnings.push(p.threshold_pct ?? 100);
        break;
      default:
        break;
    }
  }

  // Runs recorded before llm.call events were emitted on every path: trust the persisted spend.
  if (state.totalCost === 0 && run?.spent_usd) state.totalCost = run.spent_usd;

  // Overlay the persisted per-dependency outcomes (success, error, final diff, MR) when known.
  const outcomes: any[] = run?.summary?.dependencies ?? [];
  for (const outcome of outcomes) {
    const dep = depOf(state, `dep:${outcome.name}`);
    if (!dep) continue;
    dep.outcome = outcome;
    dep.success = outcome.success;
    dep.error = outcome.error ?? null;
    if (outcome.merge_request) dep.mergeRequest = { ...(dep.mergeRequest ?? {}), ...outcome.merge_request };
    if (outcome.merge_request_url) dep.mergeRequest = { ...(dep.mergeRequest ?? {}), web_url: outcome.merge_request_url };
    if (state.status !== "running") dep.status = outcome.success ? "done" : "failed";
  }
  if (state.status !== "running") {
    for (const name of state.order) {
      const d = state.deps[name];
      if (["researching", "patching", "verifying", "reviewing", "queued"].includes(d.status)) {
        d.status = d.success ? "done" : d.success === false ? "failed" : d.status;
      }
    }
  }
  return state;
}

export function latestDiff(dep: DepState): string {
  if (dep.outcome?.diff) return dep.outcome.diff;
  const numbers = Object.keys(dep.attempts).map(Number).sort((a, b) => b - a);
  for (const n of numbers) {
    if (dep.attempts[n].diff) return dep.attempts[n].diff;
  }
  return "";
}
