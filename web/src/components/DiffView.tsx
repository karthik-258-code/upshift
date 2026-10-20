export default function DiffView({ diff }: { diff: string }) {
  if (!diff) return <p className="text-sm text-zinc-500 p-4">No code changes yet.</p>;
  const lines = diff.split("\n");
  return (
    <pre className="mono text-[12px] leading-5 overflow-auto p-0 m-0">
      {lines.map((line, i) => {
        let cls = "text-zinc-300";
        if (line.startsWith("+++") || line.startsWith("---")) cls = "diff-file";
        else if (line.startsWith("@@")) cls = "diff-hunk";
        else if (line.startsWith("+")) cls = "diff-add";
        else if (line.startsWith("-")) cls = "diff-del";
        return (
          <div key={i} className={`px-4 whitespace-pre ${cls}`}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}
