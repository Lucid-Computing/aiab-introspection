/** Small form controls shared by the inspector. */
import { python } from "@codemirror/lang-python";
import CodeMirror from "@uiw/react-codemirror";
import { type ReactNode, useEffect, useState } from "react";

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
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={(e) => {
        const v = Number(e.target.value);
        if (e.target.value !== "" && Number.isFinite(v) && (min === undefined || v >= min) && (max === undefined || v <= max))
          onChange(v);
      }}
    />
  );
}

/** One entry per line. */
export function LinesInput({ value, onChange, rows = 3 }: { value: string[]; onChange: (v: string[]) => void; rows?: number }) {
  const [text, setText] = useState(value.join("\n"));
  useEffect(() => {
    if (text.split("\n").filter((l) => l.trim()).join("\n") !== value.join("\n")) setText(value.join("\n"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <textarea
      rows={rows}
      value={text}
      onChange={(e) => {
        setText(e.target.value);
        onChange(e.target.value.split("\n").map((l) => l.trim()).filter(Boolean));
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
