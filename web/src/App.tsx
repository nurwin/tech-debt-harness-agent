import { useCallback, useEffect, useState } from "react";
import { api, RunSummary } from "./api";
import RunDetail from "./components/RunDetail";
import RunList from "./components/RunList";

export default function App() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listRuns();
      list.sort((a, b) => a.thread_id.localeCompare(b.thread_id));
      setRuns(list);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [refresh]);

  return (
    <div className="min-h-screen p-6 max-w-7xl mx-auto">
      <header className="mb-6 flex items-baseline gap-4">
        <h1 className="text-2xl font-bold tracking-tight">Refactor Harness</h1>
        <span className="text-sm text-slate-400">
          LangGraph orchestrator · Pi executor · HITL-gated
        </span>
        {error && <span className="text-sm text-red-400 ml-auto">API unreachable: {error}</span>}
      </header>
      <div className="grid grid-cols-1 lg:grid-cols-[340px_1fr] gap-6">
        <RunList
          runs={runs}
          selected={selected}
          onSelect={setSelected}
          onStarted={(id) => {
            setSelected(id);
            refresh();
          }}
        />
        {selected ? (
          <RunDetail threadId={selected} onChanged={refresh} />
        ) : (
          <div className="rounded-xl border border-slate-800 p-10 text-slate-500 text-center">
            Start a run or select one on the left.
          </div>
        )}
      </div>
    </div>
  );
}
