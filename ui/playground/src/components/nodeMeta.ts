import { LOG_SOURCES, selectRecords } from "../plan/mockLogs";
import type { NodeType, PlanNode } from "../plan/types";

export const NODE_META: Record<NodeType, { label: string; glyph: string; blurb: string }> = {
  input_logs: { label: "Input Logs", glyph: "▤", blurb: "Where the audit starts: which log records are read." },
  classifier: { label: "Classifier", glyph: "◆", blurb: "Labels each session or record with a fixed set of classes." },
  run_python: { label: "Run Python", glyph: "λ", blurb: "Deterministic computation: group, count, combine, threshold." },
  run_llm: { label: "Run LLM", glyph: "✦", blurb: "A general model call with your prompts and a declared output." },
  final_report: { label: "Final Report", glyph: "■", blurb: "The only result that leaves the box, with its release policy." },
};

export const MODEL_OPTIONS = ["gpt-oss-safeguard-120b", "gpt-oss-120b", "llama-3.3-70b-instruct", "qwen3-32b"];

/** Short human-readable lines describing what a node does. */
export function summary(n: PlanNode): string[] {
  switch (n.type) {
    case "input_logs": {
      const c = n.config;
      const recs = selectRecords(n);
      return [
        LOG_SOURCES[c.source]?.label ?? c.source,
        `${c.input_scope.agents.length ? c.input_scope.agents.join(", ") : "all agents"} · ${c.filter.kinds.length === 4 ? "all kinds" : c.filter.kinds.join(", ")}`,
        ...(c.filter.keywords.length ? [`keywords: ${c.filter.keywords.join(", ")}`] : []),
        `${recs.length} records in preview`,
      ];
    }
    case "classifier": {
      const c = n.config;
      return [
        firstLine(c.instructions) || "(no instructions)",
        `per ${c.group_by} · ${c.model.id}${c.threshold !== undefined ? ` · ≥ ${c.threshold}` : ""}`,
      ];
    }
    case "run_python": {
      const def = n.config.code.split("\n").find((l) => l.trimStart().startsWith("def ")) ?? "";
      const lines = n.config.code.trimEnd().split("\n").length;
      return [def.trim() || "(no function)", `${lines} line${lines === 1 ? "" : "s"} of Python`];
    }
    case "run_llm": {
      const c = n.config;
      return [
        firstLine(c.user_prompt) || "(empty prompt)",
        `${c.model.id} · ${c.output.type === "Items" ? "once per item" : "one call"}`,
      ];
    }
    case "final_report": {
      const p = n.config.privacy;
      return [
        `${p.allow_free_text ? "free text allowed" : "no free text"} · ≤ ${p.max_result_bytes} B${p.min_count > 1 ? ` · counts ≥ ${p.min_count}` : ""}`,
      ];
    }
  }
}

function firstLine(s: string): string {
  const l = s.trim().split("\n")[0] ?? "";
  return l.length > 70 ? `${l.slice(0, 68)}…` : l;
}
