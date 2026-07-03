import { useState } from "react";
import { PendingGate, PlanStep } from "../api";

export default function PlanGate({
  pending,
  onDecide,
}: {
  pending: PendingGate;
  onDecide: (d: { action: string; plan?: PlanStep[]; reason?: string }) => void;
}) {
  const [steps, setSteps] = useState<PlanStep[]>(pending.plan ?? []);
  const [edited, setEdited] = useState(false);

  const update = (i: number, field: keyof PlanStep, value: string) => {
    setSteps((prev) => prev.map((s, j) => (j === i ? { ...s, [field]: value } : s)));
    setEdited(true);
  };

  return (
    <div className="rounded-xl border border-amber-700 bg-amber-950/30 p-4 space-y-3">
      <h3 className="font-semibold text-amber-300 text-sm">
        ⏸ Plan gate — approve the refactoring plan before any executor tokens are spent
      </h3>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-slate-400">
            <th className="pr-2 py-1">#</th>
            <th className="pr-2">file</th>
            <th className="pr-2">type</th>
            <th>rationale (editable)</th>
          </tr>
        </thead>
        <tbody>
          {steps.map((s, i) => (
            <tr key={s.step_id} className="align-top border-t border-slate-800">
              <td className="pr-2 py-1.5 text-slate-500">{s.step_id}</td>
              <td className="pr-2 py-1.5 font-mono">{s.file}</td>
              <td className="pr-2 py-1.5">
                <input
                  value={s.change_type}
                  onChange={(e) => update(i, "change_type", e.target.value)}
                  className="w-20 rounded bg-slate-900 border border-slate-700 px-1 py-0.5"
                />
              </td>
              <td className="py-1.5">
                <textarea
                  value={s.rationale}
                  onChange={(e) => update(i, "rationale", e.target.value)}
                  rows={2}
                  className="w-full rounded bg-slate-900 border border-slate-700 px-1.5 py-1"
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex gap-2">
        <button
          onClick={() => (edited ? onDecide({ action: "edit", plan: steps }) : onDecide({ action: "approve" }))}
          className="rounded bg-emerald-600 hover:bg-emerald-500 px-3 py-1.5 text-sm font-medium"
        >
          {edited ? "Approve edited plan" : "Approve plan"}
        </button>
        <button
          onClick={() => onDecide({ action: "reject", reason: "rejected from web UI" })}
          className="rounded bg-rose-700 hover:bg-rose-600 px-3 py-1.5 text-sm font-medium"
        >
          Reject
        </button>
      </div>
    </div>
  );
}
