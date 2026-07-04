import { useEffect, useState } from "react";
import { api, Decision, openRunSocket, PendingGate, PublicState } from "../api";
import DiffView from "./DiffView";
import EscalationGate from "./EscalationGate";
import LoopTimeline from "./LoopTimeline";
import MergeGate from "./MergeGate";
import PipelineView from "./PipelineView";
import PlanGate from "./PlanGate";
import { StatusBadge } from "./RunList";
import TokenMeter from "./TokenMeter";

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-800 p-4">
      <h3 className="text-xs uppercase tracking-wide text-slate-500 mb-3">{title}</h3>
      {children}
    </div>
  );
}

export default function RunDetail({
  threadId,
  onChanged,
}: {
  threadId: string;
  onChanged: () => void;
}) {
  const [state, setState] = useState<PublicState | null>(null);
  const [pending, setPending] = useState<PendingGate | null>(null);
  const [diff, setDiff] = useState<string>("");
  const [traceUrl, setTraceUrl] = useState<string>("");
  const [wsError, setWsError] = useState<string | null>(null);

  useEffect(() => {
    setState(null);
    setPending(null);
    setDiff("");
    // initial snapshot over REST, then live over WS
    api.getState(threadId).then(setState).catch(() => {});
    api.getPending(threadId).then((p) => setPending(p.pending)).catch(() => {});
    api.getTrace(threadId).then((t) => setTraceUrl(t.url)).catch(() => {});
    const close = openRunSocket(threadId, (e) => {
      if (e.state) setState(e.state);
      setPending(e.pending ?? null);
      if (e.type === "error") setWsError(e.error ?? "unknown error");
      if (e.type === "final") onChanged();
    });
    return close;
  }, [threadId, onChanged]);

  useEffect(() => {
    if (state?.has_final_diff) api.getDiff(threadId).then((d) => setDiff(d.diff));
  }, [state?.has_final_diff, threadId]);

  if (!state)
    return (
      <div className="rounded-xl border border-slate-800 p-10 text-slate-500 text-center">
        loading {threadId}…
      </div>
    );

  const decide = async (d: Decision) => {
    setPending(null); // optimistic: gate is being consumed
    try {
      await api.decide(threadId, d);
    } catch (e) {
      setWsError(String(e));
    }
  };

  const terminal = ["succeeded", "aborted", "failed"].includes(state.status);

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-800 p-4 flex items-center gap-3 flex-wrap">
        <span className="font-mono text-sm">{state.thread_id}</span>
        <StatusBadge status={state.status} />
        <span className="text-xs text-slate-400">tenant: {state.tenant_id}</span>
        <span className="text-xs text-slate-400">adapter: {state.executor_adapter}</span>
        {state.source_repo_url && (
          <a
            href={state.source_repo_url.replace(/\.git$/, "")}
            target="_blank"
            rel="noreferrer"
            className="text-xs text-sky-400 hover:underline"
            title="target repo imported from GitHub"
          >
            imported ↗
          </a>
        )}
        {state.auto_approve && <span className="text-xs text-amber-400">auto-approve</span>}
        <a
          href={traceUrl}
          target="_blank"
          rel="noreferrer"
          className="ml-auto text-xs text-sky-400 hover:underline"
        >
          Jaeger trace ↗
        </a>
        {!terminal && !pending && (
          <button
            onClick={() => api.resume(threadId).catch((e) => setWsError(String(e)))}
            className="text-xs rounded border border-slate-700 px-2 py-1 hover:bg-slate-900"
            title="crash recovery: continue from the last checkpoint"
          >
            Resume
          </button>
        )}
      </div>

      {wsError && (
        <div className="rounded-lg border border-red-800 bg-red-950/40 p-3 text-xs text-red-300">
          {wsError}
        </div>
      )}
      {state.failure_reason && (
        <div className="rounded-lg border border-amber-800 bg-amber-950/40 p-3 text-xs text-amber-300">
          {state.failure_reason}
        </div>
      )}

      <Panel title="Pipeline">
        <PipelineView state={state} />
      </Panel>

      {pending?.gate === "plan" && <PlanGate pending={pending} onDecide={decide} />}
      {pending?.gate === "escalation" && <EscalationGate pending={pending} onDecide={decide} />}
      {pending?.gate === "merge" && <MergeGate pending={pending} onDecide={decide} />}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Panel title="Token usage (per node)">
          <TokenMeter usage={state.token_usage} />
        </Panel>
        <Panel title="Verification loop (chips = iterations)">
          <LoopTimeline state={state} />
        </Panel>
      </div>

      {state.plan.length > 0 && (
        <Panel title="Plan">
          <ul className="space-y-1 text-xs">
            {state.plan.map((s) => (
              <li key={s.step_id} className="flex gap-2">
                <span className="text-slate-500">{s.step_id}.</span>
                <span className="font-mono">{s.file}</span>
                <span className="text-slate-400">{s.rationale}</span>
                <span className="ml-auto text-slate-500">{s.status}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {state.approval_history.length > 0 && (
        <Panel title="Approval audit trail">
          <ul className="space-y-1 text-xs font-mono">
            {state.approval_history.map((d, i) => (
              <li key={i} className="text-slate-400">
                <span className="text-slate-500">{d.timestamp}</span>{" "}
                <span className="text-sky-300">{d.gate}</span> → {d.action}{" "}
                <span className="text-slate-500">by {d.actor}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {diff && (
        <Panel title="Final diff">
          <DiffView diff={diff} />
        </Panel>
      )}
    </div>
  );
}
