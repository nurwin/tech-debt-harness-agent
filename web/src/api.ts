// The ONE typed client for REST + WebSocket — components never fetch inline.

export interface PlanStep {
  step_id: number;
  file: string;
  change_type: string;
  rationale: string;
  status: "pending" | "in_progress" | "done" | "failed";
}

export interface ErrorRecord {
  step_id: number;
  iteration: number;
  stdout: string;
  stderr: string;
  failed_tests: string[];
  lint_errors: string[];
  timestamp: string;
}

export interface HumanDecision {
  gate: string;
  action: string;
  actor: string;
  timestamp: string;
  payload: Record<string, unknown> | null;
}

export interface TokenUsage {
  planner: number;
  executor: number;
  verifier: number;
  total: number;
}

export interface PublicState {
  thread_id: string;
  tenant_id: string;
  status: string;
  auto_approve: boolean;
  executor_adapter: string;
  plan: PlanStep[];
  current_step: number;
  completed_steps: number[];
  iteration_count: number;
  escalation_count: number;
  error_log: ErrorRecord[];
  baseline_failed_tests: string[];
  baseline_lint_errors: string[];
  last_verification: Record<string, unknown> | null;
  pending_approval: string | null;
  approval_history: HumanDecision[];
  token_usage: TokenUsage;
  failure_reason: string | null;
  has_final_diff: boolean;
  source_repo_url: string | null;
}

export interface RunSummary {
  thread_id: string;
  tenant_id: string;
  status: string;
  pending_approval: string | null;
  current_step: number;
  plan_length: number;
  completed_steps: number[];
  iteration_count: number;
  token_total: number;
  failure_reason: string | null;
}

export interface PendingGate {
  gate: "plan" | "escalation" | "merge";
  plan?: PlanStep[];
  diff?: string;
  step?: PlanStep;
  errors?: ErrorRecord[];
  iteration_count?: number;
  escalation_count?: number;
  completed_steps?: number[];
  token_usage?: TokenUsage;
}

export interface Decision {
  action: string;
  actor?: string;
  plan?: PlanStep[];
  guidance?: string;
  reason?: string;
}

export interface WsEvent {
  type: "state" | "final" | "error";
  state?: PublicState;
  pending?: PendingGate;
  error?: string;
}

const BASE = "";

async function j<T>(promise: Promise<Response>): Promise<T> {
  const res = await promise;
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

const post = (url: string, body: unknown) =>
  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const api = {
  listRuns: () => j<RunSummary[]>(fetch(`${BASE}/runs`)),
  startRun: (tenant_id: string, auto_approve: boolean, executor_adapter: string, repo_url?: string) =>
    j<{ thread_id: string }>(
      post(`${BASE}/runs`, { tenant_id, auto_approve, executor_adapter, repo_url: repo_url || null })
    ),
  getState: (id: string) => j<PublicState>(fetch(`${BASE}/runs/${id}/state`)),
  getPending: (id: string) =>
    j<{ pending: PendingGate | null }>(fetch(`${BASE}/runs/${id}/pending`)),
  decide: (id: string, decision: Decision) =>
    j<{ resumed: boolean }>(post(`${BASE}/runs/${id}/decision`, { actor: "human:web-ui", ...decision })),
  resume: (id: string) => j<{ resumed: boolean }>(post(`${BASE}/runs/${id}/resume`, {})),
  getDiff: (id: string) => j<{ diff: string }>(fetch(`${BASE}/runs/${id}/diff`)),
  getTrace: (id: string) =>
    j<{ url: string; exporting: boolean }>(fetch(`${BASE}/runs/${id}/trace`)),
};

export function openRunSocket(id: string, onEvent: (e: WsEvent) => void): () => void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let ws: WebSocket | null = null;
  let closed = false;

  const connect = () => {
    ws = new WebSocket(`${proto}://${location.host}/ws/runs/${id}`);
    ws.onmessage = (msg) => onEvent(JSON.parse(msg.data) as WsEvent);
    ws.onclose = () => {
      if (!closed) setTimeout(connect, 1500); // auto-reconnect
    };
  };
  connect();
  return () => {
    closed = true;
    ws?.close();
  };
}
