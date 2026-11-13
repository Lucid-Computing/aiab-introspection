/** Small form controls shared by the inspector. */
import { python } from "@codemirror/lang-python";
import CodeMirror from "@uiw/react-codemirror";
import { Children, Fragment, isValidElement, type ReactNode, useEffect, useRef, useState } from "react";

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="f">
      <span className="f-label">{label}</span>
      {children}
      {hint && <span className="f-hint">{hint}</span>}
    </label>
  );
}

/** A text input that commits on blur or Enter (for ids and names, where each keystroke would rename). */
export function CommitInput({
  value,
  onCommit,
  className,
  placeholder,
}: {
  value: string;
  onCommit: (v: string) => void;
  className?: string;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => {
    if (draft !== value) onCommit(draft.trim());
  };
  return (
    <input
      className={className}
      value={draft}
      placeholder={placeholder}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") setDraft(value);
      }}
    />
  );
}

export function NumberInput({
  value,
  onChange,
  min,
  max,
  step,
  label,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  /** The name a screen reader gives the box, when no visible label names it. */
  label?: string;
}) {
  return (
    <input
      type="number"
      aria-label={label}
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={(e) => {
        const v = Number(e.target.value);
        if (
          e.target.value !== "" &&
          Number.isFinite(v) &&
          (min === undefined || v >= min) &&
          (max === undefined || v <= max)
        )
          onChange(v);
      }}
    />
  );
}

/** One entry per line. */
export function LinesInput({
  value,
  onChange,
  rows = 3,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  rows?: number;
}) {
  const [text, setText] = useState(value.join("\n"));
  useEffect(() => {
    if (
      text
        .split("\n")
        .filter((l) => l.trim())
        .join("\n") !== value.join("\n")
    )
      setText(value.join("\n"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <textarea
      rows={rows}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onChange(
          e.target.value
            .split("\n")
            .map((l) => l.trim())
            .filter(Boolean),
        );
      }}
    />
  );
}

const pythonExt = [python()];

export function PythonEditor({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="cm-wrap">
      <CodeMirror
        value={value}
        onChange={onChange}
        extensions={pythonExt}
        basicSetup={{ foldGutter: false, highlightActiveLine: false }}
        minHeight="180px"
        maxHeight="420px"
      />
    </div>
  );
}

/**
 * A segmented control: two or three choices side by side, one on. A radio group to a
 * screen reader; the arrow keys move the choice, Tab leaves the group.
 */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (k: number) => {
    const next = (k + options.length) % options.length;
    onChange(options[next].value);
    refs.current[next]?.focus();
  };
  return (
    <div className={`seg${className ? ` ${className}` : ""}`} role="radiogroup" aria-label={label}>
      {options.map((o, k) => (
        <button
          key={o.value}
          ref={(el) => {
            refs.current[k] = el;
          }}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          tabIndex={o.value === value ? 0 : -1}
          className={o.value === value ? "on" : undefined}
          onClick={() => o.value !== value && onChange(o.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowRight" || e.key === "ArrowDown") {
              e.preventDefault();
              move(k + 1);
            } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
              e.preventDefault();
              move(k - 1);
            }
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** The text of a node: strings and numbers joined, elements' children followed. */
function textOf(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement(node)) return textOf((node.props as { children?: ReactNode }).children);
  return "";
}

/** The <option> children of a Select, through fragments and arrays, as value and label. */
function optionsOf(children: ReactNode, out: { value: string; label: string }[] = []) {
  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return;
    const props = child.props as { value?: string | number; children?: ReactNode };
    if (child.type === Fragment) optionsOf(props.children, out);
    else if (child.type === "option") {
      const label = textOf(props.children);
      out.push({ value: props.value === undefined ? label : String(props.value), label });
    }
  });
  return out;
}

/**
 * The site's own dropdown, used like a native <select> (value, onChange with
 * `e.target.value`, <option> children). The list opens in the flow of the page, under
 * the button, so nothing can clip or cover it.
 */
export function Select({
  value,
  onChange,
  className,
  label,
  children,
}: {
  value: string | number;
  onChange: (e: { target: { value: string } }) => void;
  className?: string;
  /** The name a screen reader gives the button, when no visible label names it. */
  label?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const options = optionsOf(children);
  const current = options.find((o) => o.value === String(value));
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <div className={`sel${open ? " is-open" : ""}${className ? ` ${className}` : ""}`} ref={box}>
      <button
        type="button"
        className="sel-btn"
        aria-label={label ? `${label}: ${current?.label ?? ""}` : undefined}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={(e) => {
          e.preventDefault();
          setOpen((x) => !x);
        }}
      >
        <span className="sel-value">{current?.label ?? ""}</span>
        <span className="sel-caret" aria-hidden="true" />
      </button>
      {open && (
        <ul className="sel-list" role="listbox">
          {options.map((o) => (
            <li key={o.value} role="option" aria-selected={o.value === String(value)}>
              <button
                type="button"
                className={o.value === String(value) ? "on" : ""}
                onClick={(e) => {
                  e.preventDefault();
                  setOpen(false);
                  if (o.value !== String(value)) onChange({ target: { value: o.value } });
                }}
              >
                {o.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
