import { useState } from "react";
import { api, RunSummary } from "../api";

export const STATUS_STYLES: Record<string, string> = {
  planning: "bg-sky-900 text-sky-200",
  awaiting_human: "bg-amber-900 text-amber-200",
  executing: "bg-indigo-900 text-indigo-200",
  verifying: "bg-violet-900 text-violet-200",
  finalizing: "bg-teal-900 text-teal-200",
  succeeded: "bg-emerald-900 text-emerald-200",
  failed: "bg-red-900 text-red-200",
  aborted: "bg-rose-950 text-rose-300",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_STYLES[status] ?? "bg-slate-800"}`}>
      {status}
    </span>
  );
}

export default function RunList({
  runs,
  selected,
  onSelect,
  onStarted,
}: {
  runs: RunSummary[];
  selected: string | null;
  onSelect: (id: string) => void;
  onStarted: (id: string) => void;
}) {
  const [tenant, setTenant] = useState("tenant-a");
  const [adapter, setAdapter] = useState("dryrun");
  const [autoApprove, setAutoApprove] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const start = async () => {
    setBusy(true);
    setErr(null);
    try {
      const { thread_id } = await api.startRun(tenant, autoApprove, adapter);
      onStarted(thread_id);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-800 p-4 space-y-3">
        <h2 className="font-semibold text-sm text-slate-300">New run</h2>
        <label className="block text-xs text-slate-400">
          Tenant
          <input
            value={tenant}
            onChange={(e) => setTenant(e.target.value)}
            className="mt-1 w-full rounded bg-slate-900 border border-slate-700 px-2 py-1.5 text-sm"
          />
        </label>
        <label className="block text-xs text-slate-400">
          Executor adapter
          <select
            value={adapter}
            onChange={(e) => setAdapter(e.target.value)}
            className="mt-1 w-full rounded bg-slate-900 border border-slate-700 px-2 py-1.5 text-sm"
          >
            <option value="dryrun">dryrun (no LLM, known fix)</option>
            <option value="flaky">flaky (fails 2× then fixes — demo the loop)</option>
            <option value="alwaysfail">alwaysfail (demo abort-at-3 + rollback)</option>
            <option value="pi">pi (real executor in Docker sandbox)</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={autoApprove}
            onChange={(e) => setAutoApprove(e.target.checked)}
          />
          auto-approve (bypass gates, tenant policy)
        </label>
        <button
          onClick={start}
          disabled={busy || !tenant}
          className="w-full rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 px-3 py-1.5 text-sm font-medium"
        >
          {busy ? "starting…" : "Start refactor run"}
        </button>
        {err && <p className="text-xs text-red-400">{err}</p>}
      </div>

      <div className="rounded-xl border border-slate-800 divide-y divide-slate-800">
        {runs.length === 0 && (
          <p className="p-4 text-sm text-slate-500">No runs yet.</p>
        )}
        {runs.map((r) => (
          <button
            key={r.thread_id}
            onClick={() => onSelect(r.thread_id)}
            className={`w-full text-left p-3 hover:bg-slate-900 ${selected === r.thread_id ? "bg-slate-900" : ""}`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-xs truncate">{r.thread_id}</span>
              <StatusBadge status={r.status} />
            </div>
            <div className="mt-1 flex gap-3 text-xs text-slate-400">
              <span>{r.tenant_id}</span>
              <span>
                step {Math.min(r.current_step + 1, Math.max(r.plan_length, 1))}/{r.plan_length || "?"}
              </span>
              <span>{r.token_total} tok</span>
              {r.pending_approval && (
                <span className="text-amber-400">gate: {r.pending_approval}</span>
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
