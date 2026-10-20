import { formatUsd, shortModel } from "../api";
import type { DepState, RunState } from "../state";

const TIER_COLOR: Record<string, string> = {
  Nano: "bg-sky-400",
  Lightning: "bg-cyan-400",
  Super: "bg-violet-400",
  Ultra: "bg-fuchsia-400",
};

export default function CostStrip({ state, dep }: { state: RunState; dep: DepState | null }) {
  const total = state.totalCost;
  const budget = state.budgetCap || 1;
  const models = Object.entries(state.costByModel).sort((a, b) => b[1].cost - a[1].cost);
  const tests = dep?.tests;
  const before = dep?.afterBump;

  return (
    <div className="grid grid-cols-[1.2fr_1fr_1fr] gap-6 px-5 py-3 border-t border-zinc-800 bg-zinc-950 text-xs">
      <div>
        <div className="flex items-baseline justify-between">
          <span className="text-zinc-500 uppercase tracking-wide text-[10px]">Token Factory spend</span>
          <span className="mono text-zinc-200">
            {formatUsd(total)} <span className="text-zinc-500">of {formatUsd(state.budgetCap, 2)}</span>
          </span>
        </div>
        <div className="mt-1.5 h-2 rounded-full bg-zinc-800 overflow-hidden flex">
          {models.map(([id, m]) => (
            <div
              key={id}
              title={`${shortModel(id)} ${formatUsd(m.cost)} · ${m.calls} calls`}
              className={`${TIER_COLOR[shortModel(id)] ?? "bg-zinc-400"} h-full`}
              style={{ width: `${Math.max(1, (m.cost / budget) * 100)}%` }}
            />
          ))}
        </div>
        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-zinc-400">
          {models.map(([id, m]) => (
            <span key={id} className="inline-flex items-center gap-1.5">
              <i className={`inline-block w-2 h-2 rounded-sm ${TIER_COLOR[shortModel(id)] ?? "bg-zinc-400"}`} />
              {shortModel(id)} <span className="mono">{formatUsd(m.cost)}</span>
              <span className="text-zinc-600">{m.calls}×</span>
            </span>
          ))}
          {models.length === 0 && <span className="text-zinc-600">no model calls yet</span>}
        </div>
      </div>

      <div>
        <div className="text-zinc-500 uppercase tracking-wide text-[10px]">Tests {dep ? `· ${dep.name}` : ""}</div>
        {tests ? (
          <div className="mt-1.5">
            <div className="h-2 rounded-full bg-zinc-800 overflow-hidden flex">
              <div className="bg-emerald-400 h-full" style={{ width: `${(tests.passed / Math.max(tests.tests, 1)) * 100}%` }} />
              <div className="bg-rose-400 h-full" style={{ width: `${(tests.failure_count / Math.max(tests.tests, 1)) * 100}%` }} />
            </div>
            <div className="mt-1.5 text-zinc-400">
              <span className="text-emerald-300 mono">{tests.passed}</span> passing ·{" "}
              <span className="text-rose-300 mono">{tests.failure_count}</span> failing of{" "}
              <span className="mono">{tests.tests}</span> collected
              {before && (
                <span className="text-zinc-600">
                  {" "}(after bare bump: {before.passed}/{before.tests})
                </span>
              )}
            </div>
          </div>
        ) : (
          <div className="mt-1.5 text-zinc-600">no verification yet</div>
        )}
      </div>

      <div>
        <div className="text-zinc-500 uppercase tracking-wide text-[10px]">Agents</div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {["supervisor", "scout", "historian", "surgeon", "verifier", "reviewer", "scribe"].map((agent) => {
            const last = state.activeAgents[agent];
            const spent = state.costByAgent[agent];
            return (
              <span
                key={agent}
                className={`px-2 py-0.5 rounded border text-[11px] ${last ? "border-zinc-700 text-zinc-200" : "border-zinc-900 text-zinc-600"}`}
                title={last ? `last: ${last}` : "idle"}
              >
                {agent}
                {spent ? <span className="mono text-zinc-500"> {formatUsd(spent, 3)}</span> : null}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}
