import { useState } from "react";
import { formatUsd, shortModel } from "../api";
import { latestDiff, type DepState } from "../state";
import DiffView from "./DiffView";

type Tab = "evidence" | "diff" | "attempts" | "review" | "mr";

const CATEGORY_TONE: Record<string, string> = {
  breaking: "text-rose-300 border-rose-500/30 bg-rose-500/10",
  deprecation: "text-amber-300 border-amber-500/30 bg-amber-500/10",
  behavior: "text-violet-300 border-violet-500/30 bg-violet-500/10",
  feature: "text-sky-300 border-sky-500/30 bg-sky-500/10",
  noise: "text-zinc-500 border-zinc-700",
};

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="p-5 text-sm text-zinc-500">{children}</p>;
}

function Evidence({ dep }: { dep: DepState }) {
  const dossier = dep.dossier;
  return (
    <div className="p-4 space-y-4">
      {dep.sources.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-500 mb-1.5">Sources</div>
          <ul className="space-y-1 text-xs">
            {dep.sources.map((s, i) => (
              <li key={i} className="flex items-center gap-2">
                <span className={`w-1.5 h-1.5 rounded-full ${s.fetched ? "bg-emerald-400" : "bg-zinc-600"}`} />
                <a className="text-zinc-300 hover:text-white truncate" href={s.url} target="_blank" rel="noreferrer">{s.url}</a>
                <span className="text-zinc-600 shrink-0">{s.fetched ? `${s.chars} chars` : s.error}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {!dossier && <Empty>Historian has not produced the Impact Dossier yet.</Empty>}
      {dossier && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-500 mb-1.5">
            Impact dossier · {dossier.relevant_count} relevant of {(dossier.entries ?? []).length} · {shortModel(dossier.model)} · {formatUsd(dossier.cost_usd)}
          </div>
          {dossier.summary && <p className="text-sm text-zinc-300 mb-3">{dossier.summary}</p>}
          {dossier.requires_new_dependencies?.length > 0 && (
            <p className="text-xs text-amber-300 mb-3">New dependencies required: {dossier.requires_new_dependencies.join(", ")}</p>
          )}
          <ul className="space-y-2">
            {(dossier.entries ?? []).map((e: any, i: number) => (
              <li key={i} className={`rounded-md border p-3 ${e.relevant ? "border-zinc-700 bg-zinc-900/60" : "border-zinc-900 opacity-60"}`}>
                <div className="flex items-center gap-2 text-xs">
                  <span className={`px-1.5 py-0.5 rounded border text-[10px] uppercase ${CATEGORY_TONE[e.category] ?? ""}`}>{e.category}</span>
                  <span className="text-zinc-100 font-medium">{e.title}</span>
                  {e.relevant && <span className="ml-auto text-emerald-300 text-[10px]">applies here</span>}
                </div>
                {e.matched_symbols?.length > 0 && <div className="mt-1 mono text-[11px] text-cyan-300">{e.matched_symbols.join(", ")}</div>}
                {e.migration && <div className="mt-1.5 text-xs text-zinc-300">{e.migration}</div>}
                {e.quote && (
                  <blockquote className="mt-1.5 text-xs text-zinc-500 border-l-2 border-zinc-700 pl-2 italic">
                    {e.quote}{" "}
                    {e.evidence_url && <a className="not-italic text-zinc-400 hover:text-white" href={e.evidence_url} target="_blank" rel="noreferrer">↗</a>}
                  </blockquote>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Attempts({ dep }: { dep: DepState }) {
  const numbers = Object.keys(dep.attempts).map(Number).sort((a, b) => a - b);
  if (numbers.length === 0) return <Empty>No patch attempts yet.</Empty>;
  return (
    <ol className="p-4 space-y-3">
      {numbers.map((n) => {
        const a = dep.attempts[n];
        const applied = a.edits.filter((e: any) => e.status === "applied");
        const t = a.tests;
        const truncated = a.analysis.startsWith("(truncated output");
        const noOutput = n > 0 && applied.length === 0 && a.rejected.length === 0;
        return (
          <li key={n} className={`rounded-md border p-3 ${a.rolledBack ? "border-amber-500/30 bg-amber-500/5" : noOutput ? "border-zinc-800 opacity-70" : "border-zinc-800"}`}>
            <div className="flex items-center gap-2 text-xs flex-wrap">
              <span className="mono text-zinc-400">#{n}</span>
              <span className="text-zinc-200">{n === 0 ? "manifest" : a.kind === "polish" ? "polish" : shortModel(a.model)}</span>
              {noOutput ? (
                <span className="px-1.5 py-0.5 rounded border border-zinc-700 text-zinc-400 text-[10px] uppercase">{truncated ? "truncated, nothing applied" : "no usable output"}</span>
              ) : (
                <span className="text-zinc-500">{applied.length} applied · {a.rejected.length} rejected</span>
              )}
              {a.rolledBack && (
                <span className="px-1.5 py-0.5 rounded border border-amber-500/40 text-amber-300 text-[10px] uppercase" title={a.rolledBack}>rolled back</span>
              )}
              {t && (
                <span className={`ml-auto mono ${t.ok ? "text-emerald-300" : "text-rose-300"}`}>
                  {t.passed} passing / {t.failure_count} failing
                </span>
              )}
            </div>
            {a.rolledBack && <p className="mt-1 text-xs text-amber-300/80">{a.rolledBack}</p>}
            {a.analysis && <p className="mt-1.5 text-xs text-zinc-400 line-clamp-4">{a.analysis}</p>}
            {applied.length > 0 && (
              <ul className="mt-2 space-y-1">
                {applied.map((e: any, i: number) => (
                  <li key={i} className="text-xs">
                    <span className="mono text-zinc-300">{e.path}</span>
                    <span className="text-zinc-500"> · {e.justification}</span>
                    {e.evidence_url && <a className="text-zinc-500 hover:text-white" href={e.evidence_url} target="_blank" rel="noreferrer"> ↗</a>}
                  </li>
                ))}
              </ul>
            )}
            {a.rejected.length > 0 && (
              <ul className="mt-2 space-y-1">
                {a.rejected.map((r: any, i: number) => (
                  <li key={i} className="text-xs text-amber-300/90"><span className="mono">{r.path}</span> · {r.reason}</li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );
}

function Review({ dep }: { dep: DepState }) {
  const r = dep.review;
  if (!r) return <Empty>Reviewer has not run yet.</Empty>;
  const tone = r.verdict === "approve" ? "text-emerald-300" : r.verdict === "skipped" ? "text-zinc-400" : "text-amber-300";
  return (
    <div className="p-4">
      <div className="text-sm">
        Verdict: <span className={`font-semibold ${tone}`}>{r.verdict}</span>
        <span className="text-zinc-500 text-xs"> · {shortModel(r.model)} · {formatUsd(r.cost_usd)}</span>
      </div>
      {r.summary && <p className="mt-2 text-sm text-zinc-300">{r.summary}</p>}
      <ul className="mt-3 space-y-2">
        {(r.findings ?? []).map((f: any, i: number) => (
          <li key={i} className="rounded-md border border-zinc-800 p-3 text-xs">
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`px-1.5 py-0.5 rounded border text-[10px] uppercase ${f.severity === "blocker" ? "text-rose-300 border-rose-500/40" : f.severity === "major" ? "text-amber-300 border-amber-500/40" : "text-zinc-400 border-zinc-700"}`}>{f.severity}</span>
              <span className="text-zinc-500">{f.kind}{f.source === "lint" ? " · ruff" : ""}</span>
              {f.audit_status && (
                <span className={`px-1.5 py-0.5 rounded border text-[10px] uppercase ${f.audit_status === "supported" ? "text-rose-300 border-rose-500/30" : f.audit_status === "contradicted" ? "text-emerald-300 border-emerald-500/30" : f.audit_status === "revised" ? "text-sky-300 border-sky-500/30" : "text-zinc-400 border-zinc-700"}`} title="The Auditor fact-checked this finding against the diff, the installed API and the full files">
                  audit: {f.audit_status}
                </span>
              )}
              <span className="mono text-zinc-300 ml-auto">{f.file}{f.line ? `:${f.line}` : ""}</span>
            </div>
            <div className="mt-1 text-zinc-200">{f.title}</div>
            {f.detail && <div className="mt-1 text-zinc-500">{f.detail}</div>}
            {f.suggestion && <div className="mt-1 text-emerald-300/80">→ {f.suggestion}</div>}
          </li>
        ))}
        {(r.findings ?? []).length === 0 && <li className="text-xs text-zinc-500">No findings.</li>}
      </ul>
    </div>
  );
}

function MergeRequest({ dep }: { dep: DepState }) {
  const mr = dep.mergeRequest;
  if (!mr) return <Empty>No merge request drafted yet.</Empty>;
  return (
    <div className="p-4">
      {mr.web_url ? (
        <a href={mr.web_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 bg-emerald-500 hover:bg-emerald-400 text-zinc-950 font-semibold px-3 py-1.5 rounded-md text-sm">
          Open merge request {mr.iid ? `!${mr.iid}` : ""} ↗
        </a>
      ) : (
        <p className="text-xs text-zinc-500">Drafted locally (no publish target configured).</p>
      )}
      {mr.title && <h3 className="mt-4 text-sm font-semibold text-zinc-100">{mr.title}</h3>}
      {mr.description && (
        <pre className="mt-2 text-xs text-zinc-400 whitespace-pre-wrap break-words max-h-[60vh] overflow-auto">{mr.description}</pre>
      )}
    </div>
  );
}

export default function DetailPane({ dep }: { dep: DepState | null }) {
  const [tab, setTab] = useState<Tab>("evidence");
  if (!dep) return <Empty>Select a dependency to see its evidence, edits and review.</Empty>;
  const tabs: [Tab, string][] = [
    ["evidence", `Evidence${dep.dossier ? ` (${dep.dossier.relevant_count})` : ""}`],
    ["attempts", `Attempts (${Object.keys(dep.attempts).length})`],
    ["diff", "Diff"],
    ["review", dep.review ? `Review · ${dep.review.verdict}` : "Review"],
    ["mr", dep.mergeRequest?.web_url ? "Merge request ✓" : "Merge request"],
  ];
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex items-center gap-1 px-3 border-b border-zinc-800 shrink-0">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-3 py-2 text-xs border-b-2 -mb-px transition ${tab === key ? "border-emerald-400 text-zinc-100" : "border-transparent text-zinc-500 hover:text-zinc-300"}`}
          >
            {label}
          </button>
        ))}
        <span className="ml-auto text-xs text-zinc-500 mono pr-1">{dep.name} {dep.current} → {dep.latest}</span>
      </div>
      <div className="flex-1 overflow-auto min-h-0">
        {tab === "evidence" && <Evidence dep={dep} />}
        {tab === "attempts" && <Attempts dep={dep} />}
        {tab === "diff" && <DiffView diff={latestDiff(dep)} />}
        {tab === "review" && <Review dep={dep} />}
        {tab === "mr" && <MergeRequest dep={dep} />}
      </div>
    </div>
  );
}
