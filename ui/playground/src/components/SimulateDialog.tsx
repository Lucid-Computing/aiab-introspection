import { useEffect, useMemo, useRef } from "react";
import { type SimValue, simulate } from "../plan/simulate";
import type { Plan } from "../plan/types";
import { NODE_META } from "./nodeMeta";

function describe(v: SimValue): string {
  switch (v.type) {
    case "LogRecords":
      return `${v.value.length} records`;
    case "Items":
      return `${v.value.length} items`;
    case "Classifications": {
      const counts: Record<string, number> = {};
      for (const c of v.value) counts[c.label] = (counts[c.label] ?? 0) + 1;
      return `${v.value.length} classified · ${Object.entries(counts)
        .map(([l, n]) => `${l} ${n}`)
        .join(", ")}`;
    }
    case "Value":
      return JSON.stringify(v.value);
  }
}

export default function SimulateDialog({
  plan,
  blocked,
  onClose,
}: {
  plan: Plan;
  blocked: string[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => ref.current?.showModal(), []);
  const sim = useMemo(() => simulate(plan), [plan]);
  const report = plan.nodes.find((n) => n.type === "final_report");
  const max = report?.type === "final_report" ? report.config.privacy.max_result_bytes : 0;

  return (
    <dialog ref={ref} className="modal sim" onClose={onClose}>
      <div className="sim-banner">
        SIMULATED · deterministic mock values over synthetic logs. No Python was executed, no model was called, and
        nothing here is a trusted or attested audit.
      </div>
      {blocked.length > 0 && (
        <div className="sim-warn">
          The plan has {blocked.length} error{blocked.length > 1 ? "s" : ""}; the preview may be incomplete:
          <ul>
            {blocked.slice(0, 5).map((m, k) => (
              <li key={k}>{m}</li>
            ))}
          </ul>
        </div>
      )}
      <h3>Final report</h3>
      {sim.report ? (
        <>
          <pre className="contract big">{JSON.stringify(sim.report, null, 2)}</pre>
          <p className={`f-hint${sim.reportBytes > max ? " danger" : ""}`}>
            {sim.reportBytes} bytes{max ? ` of ${max} allowed` : ""}.
          </p>
        </>
      ) : (
        <p>No Final Report in the plan.</p>
      )}
      <h3>Intermediate results (simulated, never released)</h3>
      <table className="io sim-steps">
        <tbody>
          {plan.nodes
            .filter((n) => sim.outputs[n.id])
            .map((n) => (
              <tr key={n.id}>
                <td className="mono">
                  {NODE_META[n.type].glyph} {n.id}
                </td>
                <td>{describe(sim.outputs[n.id])}</td>
              </tr>
            ))}
        </tbody>
      </table>
      {sim.notes.length > 0 && (
        <>
          <h3>How values were produced</h3>
          <ul className="sim-notes">
            {sim.notes.map((n, k) => (
              <li key={k}>{n}</li>
            ))}
            <li>
              Classifier: scores from keyword overlap between each item and the label descriptions, plus a fixed hash.
            </li>
          </ul>
        </>
      )}
      <form method="dialog" className="row modal-actions">
        <button className="btn btn--primary">Close</button>
      </form>
    </dialog>
  );
}
