import { useEffect, useMemo, useRef, useState } from "react";
import { api, formatUsd, streamEvents, type Run, type UpshiftEvent } from "../api";
import { reduce } from "../state";
import CostStrip from "./CostStrip";
import DependencyColumn from "./DependencyColumn";
import DetailPane from "./DetailPane";
import Timeline from "./Timeline";

export default function RunView({ runId, readonly }: { runId: string; readonly: boolean }) {
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<UpshiftEvent[]>([]);
  const [cursor, setCursor] = useState<number | null>(null); // null = live
  const [selected, setSelected] = useState<string | null>(null);
  const [showLlm, setShowLlm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    setEvents([]);
    seen.current = 0;
    api.run(runId).then(setRun).catch((e) => setError(String(e)));
    streamEvents(
      runId,
      0,
      (event) => {
        if (event.seq <= seen.current) return;
        seen.current = event.seq;
        setEvents((prev) => [...prev, event]);
        if (event.type === "run.finished" || event.type === "run.failed") {
          api.run(runId).then(setRun).catch(() => undefined);
        }
      },
      controller.signal,
    ).catch((e) => {
      if (!controller.signal.aborted) setError(String(e));
    });
    return () => controller.abort();
  }, [runId]);

  // refresh the persisted run record periodically while running (per-dependency outcomes)
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const timer = setInterval(() => api.run(runId).then(setRun).catch(() => undefined), 10_000);
    return () => clearInterval(timer);
  }, [run?.status, runId]);

  const visible = cursor === null ? events : events.slice(0, cursor);
  const state = useMemo(() => reduce(visible, cursor === null ? run : null), [visible, run, cursor]);

  useEffect(() => {
    if (selected || state.order.length === 0) return;
    const first = state.candidates[0] ?? state.order[0];
    if (first) setSelected(first);
  }, [state.order.length, state.candidates.length, selected]);

  const dep = selected ? state.deps[selected] ?? null : null;
  const deps = state.order.map((n) => state.deps[n]);
  const live = state.status === "running" && cursor === null;
  const title = `${state.status === "running" ? "● " : ""}Upshift · ${state.repoUrl.split(/[\\/]/).pop() ?? runId}`;
  useEffect(() => {
    document.title = title;
  }, [title]);

  return (
    <div className="flex flex-col flex-1 min-h-0 h-[calc(100vh-3rem)]">
      <div className="flex items-center gap-3 px-5 h-11 border-b border-zinc-800 text-xs shrink-0">
        <span className={`px-2 py-0.5 rounded-full border uppercase tracking-wide ${state.status === "running" ? "border-sky-500/40 text-sky-300" : state.status === "succeeded" ? "border-emerald-500/40 text-emerald-300" : state.status === "failed" ? "border-rose-500/40 text-rose-300" : "border-zinc-700 text-zinc-400"}`}>
          {state.status}
        </span>
        <span className="mono text-zinc-300 truncate">{state.repoUrl || run?.repo_url}</span>
        <span className="text-zinc-600">run {runId}</span>
        <span className="text-zinc-600">policy {state.policy}</span>
        {(state.summary?.merge_requests?.length ?? 0) > 0 && (
          <a className="text-emerald-300 hover:underline" href={state.summary!.merge_requests[0]} target="_blank" rel="noreferrer">
            merge request ↗
          </a>
        )}
        <div className="ml-auto flex items-center gap-4">
          <label className="flex items-center gap-1.5 text-zinc-500 cursor-pointer">
            <input type="checkbox" checked={showLlm} onChange={(e) => setShowLlm(e.target.checked)} className="accent-emerald-400" />
            show model calls
          </label>
          <span className="mono text-zinc-300">{formatUsd(state.totalCost)}</span>
          {error && <span className="text-rose-300">{error}</span>}
        </div>
      </div>

      {events.length > 0 && (
        <div className="flex items-center gap-3 px-5 py-1.5 border-b border-zinc-900 bg-zinc-950 text-[11px] text-zinc-500 shrink-0">
          <span className="uppercase tracking-wide">{live ? "live" : cursor === null ? `complete · ${events.length} events · drag to replay` : `replay · event ${cursor} / ${events.length}`}</span>
          <input
            type="range"
            min={1}
            max={events.length}
            value={cursor ?? events.length}
            onChange={(e) => setCursor(Number(e.target.value) >= events.length ? null : Number(e.target.value))}
            className="flex-1 accent-emerald-400"
          />
          {cursor !== null && (
            <button className="text-emerald-300 hover:underline" onClick={() => setCursor(null)}>
              jump to {state.status === "running" ? "live" : "end"}
            </button>
          )}
          {!readonly && state.status !== "running" && (
            <a href="#/" className="text-zinc-400 hover:text-white">new run</a>
          )}
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        <DependencyColumn deps={deps} selected={selected} onSelect={setSelected} />
        <section className="flex-1 min-w-0 overflow-y-auto border-r border-zinc-800">
          <div className="px-4 py-2 text-[10px] uppercase tracking-wide text-zinc-500 sticky top-0 bg-zinc-950/95 backdrop-blur z-10 flex items-center gap-2">
            Agent timeline
            {selected && <span className="text-zinc-600 normal-case tracking-normal">· filtered to {selected}</span>}
            {selected && <button className="ml-auto text-zinc-400 hover:text-white normal-case tracking-normal" onClick={() => setSelected(null)}>show all</button>}
          </div>
          <Timeline events={visible} selected={selected} showLlm={showLlm} />
        </section>
        <section className="w-[42%] min-w-[420px] shrink-0 min-h-0">
          <DetailPane dep={dep} />
        </section>
      </div>

      <CostStrip state={state} dep={dep} />
    </div>
  );
}
