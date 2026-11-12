import { LOG_SOURCES } from "../plan/mockLogs";
import type { NodeType, PlanNode } from "../plan/types";

export const NODE_META: Record<NodeType, { label: string; blurb: string }> = {
  input_logs: { label: "Input filter", blurb: "Which log records the audit reads." },
  classifier: { label: "Audit", blurb: "One question the model answers about each item, with a fixed set of labels." },
  run_python: { label: "Metric", blurb: "Arithmetic between blocks, in Python the parties signed." },
  run_llm: { label: "Agent", blurb: "A free model call with your own prompts and a declared output." },
  final_report: { label: "Report", blurb: "The only result that leaves the box, with its release rules." },
};

export const MODEL_OPTIONS = ["gpt-oss-safeguard-120b", "gpt-oss-120b", "llama-3.3-70b-instruct", "qwen3-32b"];

/** The one line a block shows under its title: the setting worth seeing without opening it. */
export function line(n: PlanNode): string {
  switch (n.type) {
    case "input_logs": {
      const c = n.config;
      const parts = [LOG_SOURCES[c.source]?.label ?? c.source];
      if (c.input_scope.agents.length) parts.push(`${c.input_scope.agents.length} agent${c.input_scope.agents.length === 1 ? "" : "s"}`);
      if (c.filter.keywords.length) parts.push(`${c.filter.keywords.length} keyword${c.filter.keywords.length === 1 ? "" : "s"}`);
      return parts.join(" · ");
    }
    case "classifier": {
      const c = n.config;
      const labels = c.labels.map((l) => l.name).join(", ");
      return labels ? `labels: ${labels}` : "no labels yet";
    }
    case "run_python": {
      const lines = n.config.code.trimEnd().split("\n").length;
      return `${lines} line${lines === 1 ? "" : "s"} of Python → ${n.config.output.type}`;
    }
    case "run_llm":
      return `${n.config.model.id} → ${n.config.output.type}`;
    case "final_report": {
      const f = n.config.fields.map((x) => x.name).join(", ");
      return f ? `releases: ${f}` : "no fields yet";
    }
  }
}
