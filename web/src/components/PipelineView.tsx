import { PublicState } from "../api";

const NODES = ["planner", "plan_gate", "executor", "verifier", "merge_gate", "finalizer"];

function activeNode(s: PublicState): string {
  switch (s.status) {
    case "planning":
      return "planner";
    case "awaiting_human":
      return s.pending_approval === "plan"
        ? "plan_gate"
        : s.pending_approval === "merge"
          ? "merge_gate"
          : "verifier"; // escalation: parked after the verifier loop
    case "executing":
      return "executor";
    case "verifying":
      return "verifier";
    case "finalizing":
      return "finalizer";
    default:
      return "";
  }
}

export default function PipelineView({ state }: { state: PublicState }) {
  const active = activeNode(state);
  const terminal = ["succeeded", "aborted", "failed"].includes(state.status);
  return (
    <div className="flex items-center gap-1 flex-wrap">
      {NODES.map((n, i) => (
        <span key={n} className="flex items-center gap-1">
          {i > 0 && <span className="text-slate-600">→</span>}
          <span
            className={`px-2.5 py-1 rounded-md text-xs font-medium border ${
              n === active
                ? "border-indigo-400 bg-indigo-950 text-indigo-200 animate-pulse"
                : "border-slate-800 text-slate-400"
            }`}
          >
            {n.replace("_", " ")}
          </span>
        </span>
      ))}
      <span className="text-slate-600">→</span>
      <span
        className={`px-2.5 py-1 rounded-md text-xs font-medium border ${
          terminal
            ? state.status === "succeeded"
              ? "border-emerald-500 bg-emerald-950 text-emerald-200"
              : "border-rose-500 bg-rose-950 text-rose-200"
            : "border-slate-800 text-slate-500"
        }`}
      >
        {terminal ? state.status : "…"}
      </span>
    </div>
  );
}
