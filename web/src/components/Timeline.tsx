import { useEffect, useRef } from "react";
import { formatUsd, shortModel, type UpshiftEvent } from "../api";

const AGENT_TONE: Record<string, string> = {
  supervisor: "text-zinc-300 border-zinc-700",
  scout: "text-sky-300 border-sky-500/40",
  historian: "text-cyan-300 border-cyan-500/40",
  surgeon: "text-violet-300 border-violet-500/40",
  verifier: "text-amber-300 border-amber-500/40",
  reviewer: "text-fuchsia-300 border-fuchsia-500/40",
  scribe: "text-emerald-300 border-emerald-500/40",
  system: "text-zinc-400 border-zinc-800",
};

function describe(e: UpshiftEvent): { title: string; detail?: string; tone?: string } {
  const p = e.payload ?? {};
  switch (e.type) {
    case "run.started":
      return { title: `run started · policy ${p.policy} · budget ${formatUsd(p.budget_cap_usd, 2)}` };
    case "run.finished":
      return { title: `run ${p.status}: ${p.succeeded} upgraded, ${p.failed} failed · ${formatUsd(p.spent_usd)}`, tone: p.failed && !p.succeeded ? "rose" : "emerald" };
    case "run.failed":
      return { title: "run crashed", detail: p.error, tone: "rose" };
    case "dep.discovered":
      return { title: `${p.name} ${p.current ?? "?"} → ${p.latest ?? "?"} · ${p.bump} · risk ${p.risk}`, detail: p.skip_reason ?? (p.risk_factors ?? []).join("; ") };
    case "agent.message":
      if (p.rolled_back) return { title: p.message ?? "attempt rolled back", tone: "amber" };
      return { title: p.message ?? "", detail: Array.isArray(p.candidates) ? `candidates: ${p.candidates.join(", ")}` : undefined };
    case "tool.call":
      return { title: `web search: “${p.query}”`, detail: `${p.results} results${p.cached ? " (cached)" : ""}${p.error ? ` · ${p.error}` : ""}` };
    case "evidence.fetched":
      return { title: p.fetched ? `read ${p.url}` : `could not read ${p.url}`, detail: p.fetched ? `${p.chars} chars via ${p.via}${p.truncated ? ", truncated" : ""}` : p.error, tone: p.fetched ? undefined : "amber" };
    case "dossier.ready":
      return { title: `impact dossier: ${p.relevant_count} relevant of ${(p.entries ?? []).length} documented changes`, detail: p.error ?? p.summary, tone: p.error ? "amber" : undefined };
    case "patch.proposed":
      if (p.kind === "manifest") return { title: "manifest updated", detail: (p.changes ?? []).join("; ") };
      return { title: `attempt ${p.attempt}${p.kind === "polish" ? " (polish)" : ""} · ${shortModel(p.model)} · ${(p.edits ?? []).filter((x: any) => x.status === "applied").length} edits applied`, detail: p.analysis };
    case "patch.rejected":
      return { title: `edit rejected: ${p.path}`, detail: p.reason, tone: "amber" };
    case "sandbox.started":
      return { title: `sandbox: ${p.label}`, detail: `${p.backend} · ${p.image}` };
    case "sandbox.finished":
      return { title: `sandbox finished · ${p.phase} · exit ${p.exit_code} · ${(p.duration_ms / 1000).toFixed(1)}s` };
    case "test.result":
      return { title: `${p.passed} passing · ${p.failure_count} failing of ${p.tests}`, detail: (p.failures ?? []).slice(0, 3).map((f: any) => f.test).join(", "), tone: p.ok ? "emerald" : "rose" };
    case "escalation":
      return { title: `escalation → ${shortModel(p.to_model)}`, detail: p.reason, tone: "amber" };
    case "review.verdict":
      return { title: `review: ${p.verdict} · ${(p.findings ?? []).length} findings · ${p.blocker_count ?? 0} blocking`, detail: p.summary, tone: p.blocker_count ? "rose" : "emerald" };
    case "mr.opened":
      return { title: `merge request !${p.iid} opened`, detail: p.web_url, tone: "emerald" };
    case "llm.call":
      return { title: `${shortModel(p.model_id)} · ${p.prompt_tokens}→${p.completion_tokens} tokens · ${formatUsd(p.cost_usd)} · ${(p.latency_ms / 1000).toFixed(1)}s${p.retries ? ` · ${p.retries} retries` : ""}` };
    case "budget.warning":
      return { title: `budget ${p.threshold_pct}% used`, detail: p.message, tone: "amber" };
    default:
      return { title: e.type };
  }
}

export default function Timeline({ events, selected, showLlm }: { events: UpshiftEvent[]; selected: string | null; showLlm: boolean }) {
  const endRef = useRef<HTMLDivElement>(null);
  const filtered = events.filter((e) => {
    if (!showLlm && e.type === "llm.call") return false;
    if (!selected) return true;
    return e.node_id === `dep:${selected}` || !e.node_id;
  });
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [filtered.length]);

  return (
    <ol className="px-4 py-3 space-y-1.5">
      {filtered.map((e) => {
        const d = describe(e);
        const tone = d.tone === "rose" ? "border-l-rose-400" : d.tone === "emerald" ? "border-l-emerald-400" : d.tone === "amber" ? "border-l-amber-400" : "border-l-zinc-800";
        return (
          <li key={e.seq} className={`grid grid-cols-[64px_92px_1fr] gap-3 items-start border-l-2 ${tone} pl-3 py-1`}>
            <span className="mono text-[11px] text-zinc-600 pt-0.5">{new Date(e.ts).toLocaleTimeString([], { hour12: false })}</span>
            <span className={`text-[11px] uppercase tracking-wide border rounded px-1.5 py-0.5 text-center ${AGENT_TONE[e.agent] ?? AGENT_TONE.system}`}>{e.agent}</span>
            <div className="min-w-0">
              <div className="text-sm text-zinc-200 break-words">{d.title}</div>
              {d.detail && <div className="text-xs text-zinc-500 mt-0.5 break-words line-clamp-3">{d.detail}</div>}
            </div>
          </li>
        );
      })}
      {filtered.length === 0 && <li className="text-sm text-zinc-600">Waiting for events…</li>}
      <div ref={endRef} />
    </ol>
  );
}
