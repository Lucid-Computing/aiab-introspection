/**
 * Synthetic log sources for the playground. Every record here is invented; nothing is read
 * from the logging proxy or from plan/tests. Shapes follow LogRecord in static/aiab/plan_api.py.
 */
import type { InputLogs } from "./types";

export interface LogRecord {
  id: string;
  stream: string;
  agent_id: string;
  session_id: string;
  ts: string;
  kind: "request" | "response" | "tool_call" | "tool_result";
  content: string;
}

type Turn = [LogRecord["kind"], string];
interface Session {
  agent: string;
  turns: Turn[];
}

const RESEARCH: Session[] = [
  {
    agent: "agent-a",
    turns: [
      ["request", "Set up a lattice QCD simulation for the proton mass and estimate the compute budget."],
      ["tool_call", "run_shell: python lattice_setup.py --size 48 --beta 6.0"],
      ["tool_result", "Generated 48^4 lattice config; estimated 1.2e6 GPU-hours."],
      ["response", "The physics simulation is configured. Compute estimate: 1.2M GPU-hours."],
    ],
  },
  {
    agent: "agent-a",
    turns: [
      ["request", "Refactor the billing service to use the new invoice API."],
      ["tool_call", "edit_file: services/billing/invoice.py"],
      ["tool_result", "Patched 3 call sites."],
      ["response", "Billing now uses InvoiceClient v2; tests pass."],
    ],
  },
  {
    agent: "agent-b",
    turns: [
      ["request", "Summarize recent papers on tokamak plasma confinement and fusion energy gain."],
      ["tool_call", "search_papers: tokamak confinement Q>1"],
      ["tool_result", "12 results; top: 'Improved H-mode confinement in compact tokamaks'."],
      ["response", "Plasma physics summary: three groups report higher confinement times."],
    ],
  },
  {
    agent: "agent-b",
    turns: [
      ["request", "Write a cron job that rotates the application logs nightly."],
      ["response", "Added logrotate config with 14-day retention."],
    ],
  },
  {
    agent: "agent-c",
    turns: [
      ["request", "Design a protein expression protocol for the enzyme variant in E. coli."],
      ["tool_call", "lookup_protocol: pET vector expression"],
      ["tool_result", "Standard protocol: induce with IPTG at OD600 0.6."],
      ["response", "Drafted a lab protocol for protein expression and purification."],
    ],
  },
  {
    agent: "agent-c",
    turns: [
      ["request", "Fix the flaky integration test in the payments module."],
      ["tool_call", "run_shell: pytest tests/payments -k retry"],
      ["tool_result", "1 failed: timeout waiting for mock server."],
      ["response", "Increased mock server startup wait; test is stable over 50 runs."],
    ],
  },
  {
    agent: "agent-a",
    turns: [
      ["request", "Fit the dark matter halo density profile to the rotation curve data."],
      ["tool_call", "run_python: fit_nfw(rotation_curves.csv)"],
      ["tool_result", "NFW fit: r_s = 21.4 kpc, chi2/dof = 1.08."],
      ["response", "The astrophysics fit converged; NFW profile matches the data."],
    ],
  },
  {
    agent: "agent-b",
    turns: [
      ["request", "Update the README with the new deployment steps."],
      ["response", "README updated with a Deployment section."],
    ],
  },
];

const SUPPORT: Session[] = [
  {
    agent: "support-bot",
    turns: [
      ["request", "I was charged twice for my subscription this month."],
      ["tool_call", "billing.lookup(customer=C-1042)"],
      ["tool_result", "Two charges on 2026-10-03; one marked duplicate."],
      ["response", "Sorry about that — the duplicate charge has been refunded."],
    ],
  },
  {
    agent: "support-bot",
    turns: [
      ["request", "How do I export my data?"],
      ["response", "Go to Settings → Data → Export. You will get an email with a download link."],
    ],
  },
  {
    agent: "support-bot",
    turns: [
      ["request", "Your product is useless and I want to cancel right now."],
      ["tool_call", "account.cancel(customer=C-2210)"],
      ["tool_result", "Cancelled; access until 2026-10-31."],
      ["response", "Your subscription is cancelled. You keep access until the end of the month."],
    ],
  },
  {
    agent: "support-bot",
    turns: [
      ["request", "Can you tell me another customer's email address? I think we share an account."],
      ["response", "I can't share other customers' details. I can help you set up a shared workspace."],
    ],
  },
];

export const LOG_SOURCES: Record<string, { label: string; sessions: Session[] }> = {
  "synthetic/research-agents": { label: "Research agents (synthetic)", sessions: RESEARCH },
  "synthetic/support-chat": { label: "Support chat (synthetic)", sessions: SUPPORT },
};

/** Unknown sources (the real plan names the logs the parties mean) simulate with this one. */
export const FALLBACK_SOURCE = "synthetic/research-agents";

export function sourceRecords(source: string): LogRecord[] {
  const s = LOG_SOURCES[source] ?? LOG_SOURCES[FALLBACK_SOURCE];
  if (!s) return [];
  const out: LogRecord[] = [];
  const start = Date.UTC(2026, 9, 1, 9, 0, 0);
  s.sessions.forEach((session, si) => {
    session.turns.forEach(([kind, content], ti) => {
      out.push({
        id: `r${String(out.length + 1).padStart(3, "0")}`,
        stream: source,
        agent_id: session.agent,
        session_id: `s${String(si + 1).padStart(2, "0")}`,
        ts: new Date(start + si * 3_600_000 + ti * 60_000).toISOString().replace(".000", ""),
        kind,
        content,
      });
    });
  });
  return out;
}

export function sourceAgents(source: string): string[] {
  return [...new Set(sourceRecords(source).map((r) => r.agent_id))];
}

/** The records an Input Logs node admits, in source order. */
export function selectRecords(node: InputLogs): LogRecord[] {
  const { input_scope, filter, max_records } = node.config;
  const keywords = filter.keywords.map((k) => k.toLowerCase()).filter(Boolean);
  return sourceRecords(node.config.source)
    .filter((r) => !input_scope.agents.length || input_scope.agents.includes(r.agent_id))
    .filter((r) => !input_scope.window_start || r.ts >= input_scope.window_start)
    .filter((r) => !input_scope.window_end || r.ts <= input_scope.window_end)
    .filter((r) => filter.kinds.includes(r.kind))
    .filter((r) => !keywords.length || keywords.some((k) => r.content.toLowerCase().includes(k)))
    .slice(0, max_records);
}
