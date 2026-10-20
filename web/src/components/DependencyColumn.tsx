import type { DepState, DepStatus } from "../state";

const STATUS_META: Record<DepStatus, { label: string; dot: string; live?: boolean }> = {
  discovered: { label: "found", dot: "bg-zinc-500" },
  skipped: { label: "skipped", dot: "bg-zinc-700" },
  queued: { label: "queued", dot: "bg-zinc-400" },
  researching: { label: "researching", dot: "bg-cyan-400", live: true },
  patching: { label: "patching", dot: "bg-violet-400", live: true },
  verifying: { label: "verifying", dot: "bg-amber-400", live: true },
  reviewing: { label: "reviewing", dot: "bg-fuchsia-400", live: true },
  done: { label: "merged request open", dot: "bg-emerald-400" },
  failed: { label: "not upgraded", dot: "bg-rose-400" },
};

const BUMP_TONE: Record<string, string> = {
  major: "text-rose-300 border-rose-500/30 bg-rose-500/10",
  minor: "text-amber-300 border-amber-500/30 bg-amber-500/10",
  patch: "text-emerald-300 border-emerald-500/30 bg-emerald-500/10",
};

export default function DependencyColumn({
  deps,
  selected,
  onSelect,
}: {
  deps: DepState[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  const active = deps.filter((d) => d.status !== "skipped" && d.status !== "discovered");
  const rest = deps.filter((d) => !active.includes(d));
  return (
    <aside className="w-72 shrink-0 border-r border-zinc-800 overflow-y-auto">
      <div className="px-4 py-3 text-[10px] uppercase tracking-wide text-zinc-500 sticky top-0 bg-zinc-950">
        Dependencies <span className="text-zinc-600">({deps.length})</span>
      </div>
      {deps.length === 0 && <p className="px-4 text-sm text-zinc-600">Scouting manifests…</p>}
      <ul>
        {[...active, ...rest].map((dep) => {
          const meta = STATUS_META[dep.status];
          const isSelected = dep.name === selected;
          const symbols = Object.keys(dep.usage?.symbols ?? {}).length;
          return (
            <li key={dep.name}>
              <button
                onClick={() => onSelect(dep.name)}
                className={`w-full text-left px-4 py-3 border-b border-zinc-900 hover:bg-zinc-900/70 transition ${isSelected ? "bg-zinc-900" : ""} ${dep.status === "skipped" ? "opacity-50" : ""}`}
              >
                <div className="flex items-center gap-2">
                  <span className={`inline-block w-2 h-2 rounded-full ${meta.dot} ${meta.live ? "pulsing" : ""}`} />
                  <span className="mono text-sm text-zinc-100 truncate">{dep.name}</span>
                  {dep.bump && dep.bump !== "none" && dep.bump !== "unknown" && (
                    <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded border ${BUMP_TONE[dep.bump] ?? "text-zinc-400 border-zinc-700"}`}>
                      {dep.bump}
                    </span>
                  )}
                </div>
                <div className="mt-1 text-xs text-zinc-500 mono truncate">
                  {dep.current ?? "?"} → {dep.latest ?? "?"}
                </div>
                <div className="mt-1 flex items-center justify-between text-[11px] text-zinc-500">
                  <span>{dep.skipReason ?? meta.label}</span>
                  {symbols > 0 && <span>{symbols} symbols · {dep.usage.files.length} files</span>}
                </div>
                {dep.tests && (
                  <div className="mt-1.5 h-1 rounded-full bg-zinc-800 overflow-hidden flex">
                    <div className="bg-emerald-400" style={{ width: `${(dep.tests.passed / Math.max(dep.tests.tests, 1)) * 100}%` }} />
                    <div className="bg-rose-400" style={{ width: `${(dep.tests.failure_count / Math.max(dep.tests.tests, 1)) * 100}%` }} />
                  </div>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
