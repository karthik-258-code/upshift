import { useEffect, useState } from "react";
import { api, formatUsd, type Health, type Run } from "../api";
import { navigate } from "../App";

const DEMO = {
  repo_url: "demo-repos/pyshop-legacy",
  packages: "pydantic",
  target_project: "gitlab-ai-hackathon/transcend-october-2026/43491832/pyshop-legacy",
};

function StatusPill({ status }: { status: string }) {
  const tone: Record<string, string> = {
    succeeded: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    failed: "bg-rose-500/15 text-rose-300 border-rose-500/30",
    running: "bg-sky-500/15 text-sky-300 border-sky-500/30",
    queued: "bg-zinc-500/15 text-zinc-300 border-zinc-500/30",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full border text-[11px] uppercase tracking-wide ${tone[status] ?? tone.queued}`}>
      {status}
    </span>
  );
}

export default function Launch({ readonly }: { readonly: boolean }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [repo, setRepo] = useState(DEMO.repo_url);
  const [packages, setPackages] = useState(DEMO.packages);
  const [policy, setPolicy] = useState("balanced");
  const [budget, setBudget] = useState("2");
  const [target, setTarget] = useState(DEMO.target_project);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    api.runs().then((r) => setRuns(r.runs)).catch(() => setRuns([]));
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun({
        repo_url: repo.trim(),
        policy,
        budget_cap_usd: Number(budget) || undefined,
        packages: packages.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean),
        target_project: target.trim() || null,
      });
      navigate(`/runs/${run.id}`);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  const field = "w-full bg-zinc-900 border border-zinc-800 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-emerald-500/60";

  return (
    <div className="max-w-6xl w-full mx-auto px-5 py-10 grid lg:grid-cols-[1.1fr_1fr] gap-10">
      <section>
        <h1 className="text-3xl font-semibold tracking-tight">Upgrade a dependency. Prove it. Ship it.</h1>
        <p className="mt-3 text-zinc-400 leading-relaxed">
          Upshift reads the real changelogs, rewrites the code that breaks, runs your tests in an
          isolated sandbox until they pass, has a second model review the diff, and opens a merge
          request where every edit links to the line of documentation that justified it.
        </p>

        {!readonly && (
          <form onSubmit={submit} className="mt-8 space-y-4">
            <label className="block">
              <span className="text-xs uppercase tracking-wide text-zinc-500">Repository (git URL or local path)</span>
              <input className={`${field} mono mt-1`} value={repo} onChange={(e) => setRepo(e.target.value)} />
            </label>
            <div className="grid grid-cols-3 gap-3">
              <label className="block col-span-1">
                <span className="text-xs uppercase tracking-wide text-zinc-500">Packages (optional)</span>
                <input className={`${field} mono mt-1`} value={packages} onChange={(e) => setPackages(e.target.value)} placeholder="all actionable" />
              </label>
              <label className="block">
                <span className="text-xs uppercase tracking-wide text-zinc-500">Policy</span>
                <select className={`${field} mt-1`} value={policy} onChange={(e) => setPolicy(e.target.value)}>
                  <option value="conservative">conservative (minor, patch)</option>
                  <option value="balanced">balanced (top 3 by risk)</option>
                  <option value="aggressive">aggressive (up to 8)</option>
                </select>
              </label>
              <label className="block">
                <span className="text-xs uppercase tracking-wide text-zinc-500">Budget cap (USD)</span>
                <input className={`${field} mono mt-1`} value={budget} onChange={(e) => setBudget(e.target.value)} />
              </label>
            </div>
            <label className="block">
              <span className="text-xs uppercase tracking-wide text-zinc-500">GitLab project for the merge request (optional)</span>
              <input className={`${field} mono mt-1`} value={target} onChange={(e) => setTarget(e.target.value)} />
            </label>
            {health && health.pipeline === "noop" && (
              <p className="text-xs text-amber-300/90 bg-amber-500/10 border border-amber-500/20 rounded-md px-3 py-2">
                The API has no Token Factory or Tavily credentials; runs will only record lifecycle events.
              </p>
            )}
            {error && <p className="text-xs text-rose-300 bg-rose-500/10 border border-rose-500/20 rounded-md px-3 py-2 mono">{error}</p>}
            <button
              disabled={busy}
              className="inline-flex items-center gap-2 bg-emerald-500 hover:bg-emerald-400 disabled:opacity-50 text-zinc-950 font-semibold px-4 py-2 rounded-md transition"
            >
              {busy ? "Starting…" : "Start upgrade run"}
            </button>
          </form>
        )}

        <div className="mt-10 grid grid-cols-3 gap-3 text-xs">
          {[
            ["Models", health ? (health.capabilities_probed ? "Nemotron 3 Nano · Super · Ultra · 3.5 Lightning (probed)" : "catalog not probed") : "…"],
            ["Sandbox", health?.executor ?? "…"],
            ["Merge requests", health ? (health.gitlab_configured ? "GitLab connected" : "draft only") : "…"],
          ].map(([k, v]) => (
            <div key={k} className="rounded-md border border-zinc-800 bg-zinc-900/60 p-3">
              <div className="text-zinc-500 uppercase tracking-wide text-[10px]">{k}</div>
              <div className="mt-1 text-zinc-200">{v}</div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2 className="text-sm uppercase tracking-wide text-zinc-500">Recent runs</h2>
        <ul className="mt-3 divide-y divide-zinc-800 border border-zinc-800 rounded-md overflow-hidden">
          {runs.length === 0 && <li className="px-4 py-6 text-sm text-zinc-500">No runs yet. Start one on the left.</li>}
          {runs.map((run) => {
            const deps: any[] = run.summary?.dependencies ?? [];
            const ok = deps.filter((d) => d.success).length;
            return (
              <li key={run.id}>
                <a href={`#/runs/${run.id}`} className="flex items-center gap-3 px-4 py-3 hover:bg-zinc-900 transition">
                  <StatusPill status={run.status} />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm truncate mono">{run.repo_url}</div>
                    <div className="text-xs text-zinc-500">
                      {run.packages?.length ? run.packages.join(", ") : run.policy} · {new Date(run.created_at).toLocaleString()}
                    </div>
                  </div>
                  <div className="text-right text-xs text-zinc-400">
                    <div>{deps.length ? `${ok}/${deps.length} upgraded` : ""}</div>
                    <div className="mono">{formatUsd(run.spent_usd)}</div>
                  </div>
                </a>
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
