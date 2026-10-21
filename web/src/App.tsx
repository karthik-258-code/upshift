import { useEffect, useState } from "react";
import { isStatic } from "./api";
import Launch from "./components/Launch";
import RunView from "./components/RunView";

type Route = { name: "launch" } | { name: "run"; id: string };

function parseRoute(): Route {
  const hash = window.location.hash.replace(/^#/, "");
  const match = /^\/runs\/([A-Za-z0-9]+)/.exec(hash);
  if (match) return { name: "run", id: match[1] };
  return { name: "launch" };
}

export function navigate(path: string) {
  window.location.hash = path;
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseRoute());
  useEffect(() => {
    const onChange = () => setRoute(parseRoute());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const [replay, setReplay] = useState<boolean | null>(null);
  useEffect(() => {
    isStatic().then(setReplay);
  }, []);
  const readonly = new URLSearchParams(window.location.search).get("readonly") === "1" || replay === true;

  return (
    <div className="min-h-screen flex flex-col">
      <header className="flex items-center gap-4 px-5 h-12 border-b border-zinc-800 bg-zinc-950/80 backdrop-blur sticky top-0 z-20">
        <a href="#/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="inline-block w-2.5 h-2.5 rounded-sm bg-emerald-400" />
          Upshift
        </a>
        <span className="text-xs text-zinc-500 mono">dependency upgrades that finish the job</span>
        <div className="ml-auto flex items-center gap-3 text-xs text-zinc-500">
          {replay === true ? (
            <span className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/30" title="Recorded runs; every event, diff and review is real. Run Upshift locally to launch new ones.">
              recorded replay
            </span>
          ) : readonly ? (
            <span className="px-2 py-0.5 rounded bg-zinc-800 text-zinc-300">read-only</span>
          ) : null}
          <span>NVIDIA Nemotron on Nebius Token Factory</span>
        </div>
      </header>
      <main className="flex-1 flex flex-col">
        {replay === null ? (
          <div className="p-10 text-sm text-zinc-500">Connecting…</div>
        ) : route.name === "launch" ? (
          <Launch readonly={readonly} />
        ) : (
          <RunView runId={route.id} readonly={readonly} />
        )}
      </main>
    </div>
  );
}
