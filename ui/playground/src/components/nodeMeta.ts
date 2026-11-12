import { LOG_SOURCES } from "../plan/mockLogs";
import type { NodeType, PlanNode } from "../plan/types";

/** Each kind has a plain symbol, a name, and one sentence on why the block exists. */
export const NODE_META: Record<NodeType, { symbol: string; label: string; blurb: string }> = {
  input_logs: {
    symbol: "■",
    label: "Input filter",
    blurb: "Fixes which logs the box may read before anything runs, so the audit cannot reach beyond what both parties agreed to.",
  },
  classifier: {
    symbol: "▲",
    label: "Classifier",
    blurb: "Asks the model one fixed question about each item and keeps only a label and a score, so nothing the model writes can leave the box.",
  },
  run_python: {
    symbol: "◆",
    label: "Code",
    blurb: "Anything plain Python can compute between blocks, in code both parties read and signed; it runs sealed, with no files, network or model.",
  },
  run_llm: {
    symbol: "◇",
    label: "LLM",
    blurb: "A free model call with your own prompt, for a question the fixed audit cannot ask; its output shape is declared and checked.",
  },
  final_report: {
    symbol: "●",
    label: "Report",
    blurb: "Names the few numbers that leave the box and the rules that stop one person's session from being singled out.",
  },
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
