/**
 * Inline SVG icons for the builder: one per block kind (drawn in a coloured tile) and the
 * small ones used in a block's setting chips. 24x24 viewBox, stroke uses currentColor.
 * Every icon is decorative (aria-hidden); the element around it carries the title.
 */
import type { ReactNode } from "react";
import type { NodeType, PortType } from "../plan/types";

const PATHS = {
  // block kinds
  input_logs: (
    <>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5" />
      <path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
    </>
  ),
  classifier: (
    <>
      <path d="M3 12V3h9l9 9-9 9z" />
      <circle cx="7.5" cy="7.5" r="1.6" fill="currentColor" />
    </>
  ),
  run_python: <path d="M8 6l-6 6 6 6M16 6l6 6-6 6" strokeWidth="2.2" />,
  run_llm: (
    <g fill="currentColor" stroke="none">
      <path d="M12 2l2.2 6.8L21 11l-6.8 2.2L12 20l-2.2-6.8L3 11l6.8-2.2z" />
      <path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z" />
    </g>
  ),
  final_report: (
    <>
      <path d="M12 2l8 3v6c0 5-3.4 9.3-8 11-4.6-1.7-8-6-8-11V5z" />
      <path d="M8.5 12l2.5 2.5 4.5-5" />
    </>
  ),
  // chips
  source: <path d="M4 4h16v12H9l-5 4z" />,
  agents: (
    <>
      <rect x="5" y="8" width="14" height="11" />
      <path d="M12 8V4M9 13h.01M15 13h.01" />
    </>
  ),
  keywords: <path d="M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.5 15.5L21 21" />,
  kinds: <path d="M4 5h16l-6 8v6l-4-2v-4z" />,
  lines: <path d="M4 6h16M4 12h10M4 18h13" />,
  each: (
    <>
      <path d="M17 2l3 3-3 3" />
      <path d="M4 11V9a4 4 0 0 1 4-4h12" />
      <path d="M7 22l-3-3 3-3" />
      <path d="M20 13v2a4 4 0 0 1-4 4H4" />
    </>
  ),
  record: <path d="M5 4h14v16H5zM8 9h8M8 13h8" />,
  text: <path d="M5 5h14M12 5v15" />,
  notext: (
    <>
      <path d="M5 5h14M12 5v15" />
      <path d="M3 21L21 3" className="ic-strike" />
    </>
  ),
  size: (
    <>
      <path d="M4 4h16v16H4z" />
      <path d="M9 15l6-6M10 9h5v5" />
    </>
  ),
  people: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2 20c0-3.5 3-6 7-6s7 2.5 7 6" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M17 14c3 0 5 2 5 5" />
    </>
  ),
  warn: (
    <>
      <path d="M12 3l10 18H2z" />
      <path d="M12 10v5M12 18h.01" />
    </>
  ),
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof PATHS;

export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg
      className={`ic${className ? ` ${className}` : ""}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}

/** The block-kind icon in its coloured tile; the colour comes from `.ktile--<type>` in styles.css. */
export function KindTile({ type, small = false }: { type: NodeType; small?: boolean }) {
  return (
    <span className={`ktile ktile--${type}${small ? " ktile--sm" : ""}`}>
      <Icon name={type} />
    </span>
  );
}

/**
 * The shape that stands for a data type: on ports, in the legend and in type badges.
 * Null (unknown or not connected) is a hollow dashed circle.
 */
export function PortShape({ type }: { type: PortType | "Report" | null }) {
  return <span className={`pshape pshape--${type ?? "unknown"}`} aria-hidden="true" />;
}
