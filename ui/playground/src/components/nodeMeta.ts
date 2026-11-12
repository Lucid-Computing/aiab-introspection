import { LOG_SOURCES } from "../plan/mockLogs";
import { type NodeType, type PlanNode, RECORD_KINDS } from "../plan/types";
import type { IconName } from "./icons";

export const NODE_META: Record<NodeType, { label: string; blurb: string }> = {
  input_logs: { label: "Input Logs", blurb: "Where the audit starts: which log records are read." },
  classifier: { label: "Classifier", blurb: "Labels each session or record with a fixed set of classes." },
  run_python: {
    label: "Run Python",
    blurb: "Deterministic computation: group, count, combine, threshold.",
  },
  run_llm: { label: "Run LLM", blurb: "A general model call with your prompts and a declared output." },
  final_report: {
    label: "Final Report",
    blurb: "The only result that leaves the box, with its release policy.",
  },
};

export const MODEL_OPTIONS = ["gpt-oss-safeguard-120b", "gpt-oss-120b", "llama-3.3-70b-instruct", "qwen3-32b"];

/** One setting shown on a block: an icon, a short value, and the full meaning on hover. */
export interface Chip {
  icon?: IconName;
  text?: string;
  title: string;
}

/** The few settings worth seeing without opening a block, as icon chips. */
export function chips(n: PlanNode): Chip[] {
  switch (n.type) {
    case "input_logs": {
      const c = n.config;
      const out: Chip[] = [{ icon: "source", text: LOG_SOURCES[c.source]?.label ?? c.source, title: `Source: ${c.source}` }];
      if (c.input_scope.agents.length)
        out.push({ icon: "agents", text: String(c.input_scope.agents.length), title: `Agents: ${c.input_scope.agents.join(", ")}` });
      if (c.filter.kinds.length !== RECORD_KINDS.length)
        out.push({ icon: "kinds", text: String(c.filter.kinds.length), title: `Record kinds: ${c.filter.kinds.join(", ")}` });
      if (c.filter.keywords.length)
        out.push({ icon: "keywords", text: String(c.filter.keywords.length), title: `Keywords: ${c.filter.keywords.join(", ")}` });
      return out;
    }
    case "classifier": {
      const c = n.config;
      const out: Chip[] = [];
      if (c.group_by === "record") out.push({ icon: "record", title: "Labels each record, not each session" });
      if (c.threshold !== undefined)
        out.push({ text: `≥${c.threshold}`, title: `Abstains below confidence ${c.threshold}` });
      return out;
    }
    case "run_python": {
      const lines = n.config.code.trimEnd().split("\n").length;
      return [{ icon: "lines", text: String(lines), title: `${lines} line${lines === 1 ? "" : "s"} of Python` }];
    }
    case "run_llm":
      return n.config.output.type === "Items"
        ? [{ icon: "each", text: "each", title: `Calls ${n.config.model.id} once per item` }]
        : [{ icon: "source", text: "1", title: `Calls ${n.config.model.id} once` }];
    case "final_report": {
      const p = n.config.privacy;
      const out: Chip[] = [
        p.allow_free_text
          ? { icon: "text", title: "Free text may be released" }
          : { icon: "notext", title: "No free text is released" },
        { icon: "size", text: `${p.max_result_bytes}B`, title: `At most ${p.max_result_bytes} bytes released` },
      ];
      if (p.min_count > 1) out.push({ icon: "people", text: `≥${p.min_count}`, title: `Counts below ${p.min_count} are withheld` });
      return out;
    }
  }
}

/** The classifier's labels, for the coloured dots on its block. */
export function labelDots(n: PlanNode): { name: string; flag: boolean; description: string }[] {
  if (n.type !== "classifier") return [];
  return n.config.labels.map((l) => ({ name: l.name, flag: l.flag, description: l.description ?? "" }));
}
