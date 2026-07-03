import { PublicState } from "../api";

// One chip per verification iteration: red = failed, green = passed.
// The hard guardrail means a step never shows more than 3 red chips
// (plus at most one extra attempt granted by the escalation gate).
export default function LoopTimeline({ state }: { state: PublicState }) {
  if (state.plan.length === 0)
    return <p className="text-xs text-slate-500">No plan yet.</p>;
  return (
    <div className="space-y-2">
      {state.plan.map((step) => {
        const fails = state.error_log.filter((e) => e.step_id === step.step_id);
        const done = step.status === "done";
        const inFlight = !done && state.current_step === step.step_id;
        return (
          <div key={step.step_id} className="flex items-center gap-2 text-xs">
            <span className="w-24 truncate font-mono text-slate-400">{step.file}</span>
            <div className="flex gap-1 flex-wrap">
              {fails.map((e) => (
                <span
                  key={`${e.step_id}-${e.iteration}-${e.timestamp}`}
                  title={`iteration ${e.iteration}: ${e.failed_tests.length} failed tests, ${e.lint_errors.length} lint errors`}
                  className="w-5 h-5 rounded flex items-center justify-center bg-red-950 border border-red-700 text-red-300"
                >
                  {e.iteration}
                </span>
              ))}
              {done && (
                <span className="w-5 h-5 rounded flex items-center justify-center bg-emerald-950 border border-emerald-600 text-emerald-300">
                  ✓
                </span>
              )}
              {inFlight && !done && (
                <span className="w-5 h-5 rounded bg-slate-800 border border-slate-600 animate-pulse" />
              )}
            </div>
            <span className="ml-auto text-slate-500">{step.status}</span>
          </div>
        );
      })}
    </div>
  );
}
