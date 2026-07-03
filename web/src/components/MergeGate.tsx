import { PendingGate } from "../api";
import DiffView from "./DiffView";

export default function MergeGate({
  pending,
  onDecide,
}: {
  pending: PendingGate;
  onDecide: (d: { action: string; reason?: string }) => void;
}) {
  return (
    <div className="rounded-xl border border-teal-700 bg-teal-950/30 p-4 space-y-3">
      <h3 className="font-semibold text-teal-300 text-sm">
        ✅ Merge gate — review the final diff before finalizing
      </h3>
      <DiffView diff={pending.diff ?? ""} />
      <div className="flex gap-2">
        <button
          onClick={() => onDecide({ action: "approve" })}
          className="rounded bg-emerald-600 hover:bg-emerald-500 px-3 py-1.5 text-sm font-medium"
        >
          Approve &amp; finalize
        </button>
        <button
          onClick={() => onDecide({ action: "reject", reason: "diff rejected from web UI" })}
          className="rounded bg-rose-700 hover:bg-rose-600 px-3 py-1.5 text-sm font-medium"
        >
          Reject + roll back
        </button>
      </div>
    </div>
  );
}
