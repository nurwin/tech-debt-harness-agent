import { TokenUsage } from "../api";

const COLORS: Record<string, string> = {
  planner: "bg-sky-500",
  executor: "bg-indigo-500",
  verifier: "bg-violet-500",
};

export default function TokenMeter({ usage }: { usage: TokenUsage }) {
  const nodes = ["planner", "executor", "verifier"] as const;
  const max = Math.max(usage.total, 1);
  return (
    <div className="space-y-1.5">
      {nodes.map((n) => (
        <div key={n} className="flex items-center gap-2 text-xs">
          <span className="w-16 text-slate-400">{n}</span>
          <div className="flex-1 h-2.5 rounded bg-slate-900 overflow-hidden">
            <div
              className={`h-full ${COLORS[n]} transition-all`}
              style={{ width: `${(usage[n] / max) * 100}%` }}
            />
          </div>
          <span className="w-14 text-right font-mono text-slate-300">{usage[n]}</span>
        </div>
      ))}
      <div className="flex justify-between text-xs pt-1 border-t border-slate-800">
        <span className="text-slate-400">total</span>
        <span className="font-mono font-semibold">{usage.total} tokens</span>
      </div>
    </div>
  );
}
