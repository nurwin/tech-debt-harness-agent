import { useState } from "react";
import { PendingGate } from "../api";

export default function EscalationGate({
  pending,
  onDecide,
}: {
  pending: PendingGate;
  onDecide: (d: { action: string; guidance?: string; reason?: string }) => void;
}) {
  const [guidance, setGuidance] = useState("");
  const canRetry = (pending.escalation_count ?? 0) < 1;
  const canPartial = (pending.completed_steps ?? []).length > 0;

  return (
    <div className="rounded-xl border border-red-700 bg-red-950/30 p-4 space-y-3">
      <h3 className="font-semibold text-red-300 text-sm">
        🚨 Escalation gate — step {pending.step?.step_id} (
        <span className="font-mono">{pending.step?.file}</span>) failed{" "}
        {pending.iteration_count} verification iterations (hard cap: 3)
      </h3>
      <div className="max-h-48 overflow-auto space-y-2">
        {(pending.errors ?? []).map((e) => (
          <div key={e.iteration} className="rounded bg-slate-900 p-2 text-xs">
            <p className="text-red-400 font-medium">iteration {e.iteration}</p>
            {e.failed_tests.length > 0 && (
              <p className="text-slate-300">failed: {e.failed_tests.join(", ")}</p>
            )}
            {e.lint_errors.length > 0 && (
              <p className="text-slate-300">lint: {e.lint_errors.join(" · ")}</p>
            )}
            {e.stderr && <pre className="text-slate-500 whitespace-pre-wrap">{e.stderr.slice(-300)}</pre>}
          </div>
        ))}
      </div>
      <textarea
        value={guidance}
        onChange={(e) => setGuidance(e.target.value)}
        placeholder="Optional guidance for the retry (fed to the executor)…"
        rows={2}
        className="w-full rounded bg-slate-900 border border-slate-700 px-2 py-1.5 text-xs"
      />
      <div className="flex gap-2 flex-wrap">
        <button
          onClick={() => onDecide({ action: "retry", guidance: guidance || undefined })}
          disabled={!canRetry}
          title={canRetry ? "" : "escalation retry budget (1) already spent"}
          className="rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 px-3 py-1.5 text-sm font-medium"
        >
          Retry with guidance {canRetry ? "" : "(budget spent)"}
        </button>
        <button
          onClick={() => onDecide({ action: "accept_partial" })}
          disabled={!canPartial}
          title={canPartial ? "keep verified steps, drop the failed one" : "no verified steps to keep"}
          className="rounded bg-amber-700 hover:bg-amber-600 disabled:opacity-40 px-3 py-1.5 text-sm font-medium"
        >
          Accept partial ({(pending.completed_steps ?? []).length} steps)
        </button>
        <button
          onClick={() => onDecide({ action: "abort", reason: "aborted from web UI" })}
          className="rounded bg-rose-700 hover:bg-rose-600 px-3 py-1.5 text-sm font-medium"
        >
          Abort + roll back
        </button>
      </div>
    </div>
  );
}
