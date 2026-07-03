export default function DiffView({ diff }: { diff: string }) {
  if (!diff.trim()) return <p className="text-xs text-slate-500">Empty diff.</p>;
  return (
    <pre className="max-h-96 overflow-auto rounded bg-slate-900 p-3 text-xs leading-5">
      {diff.split("\n").map((line, i) => (
        <div
          key={i}
          className={
            line.startsWith("+") && !line.startsWith("+++")
              ? "text-emerald-400"
              : line.startsWith("-") && !line.startsWith("---")
                ? "text-rose-400"
                : line.startsWith("@@")
                  ? "text-sky-400"
                  : "text-slate-400"
          }
        >
          {line || " "}
        </div>
      ))}
    </pre>
  );
}
