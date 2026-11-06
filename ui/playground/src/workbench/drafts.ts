import { createContext, useContext, useState, type Dispatch, type SetStateAction } from "react";

type Buffer = { text: string; base: string };
export type Drafts = Record<string, Buffer>;
export const DraftContext = createContext<{ drafts: Drafts; setDrafts: Dispatch<SetStateAction<Drafts>> } | null>(null);

/** Keep typed bytes until apply/reset; a parent update requires explicit resolution. */
export function useDraft(key: string, current: string) {
  const shared = useContext(DraftContext);
  const [local, setLocal] = useState<Drafts>({});
  const drafts = shared?.drafts ?? local;
  const setDrafts = shared?.setDrafts ?? setLocal;
  const draft = drafts[key];
  const text = draft?.text ?? current;
  const conflict = !!draft && draft.base !== current;
  const setText = (text: string) => setDrafts(previous => {
    const next = { ...previous };
    if (text === current) delete next[key];
    else next[key] = { text, base: previous[key]?.base ?? current };
    return next;
  });
  const reset = () => setDrafts(previous => { const next = { ...previous }; delete next[key]; return next; });
  const rebase = () => setDrafts(previous => ({ ...previous, [key]: { text, base: current } }));
  return { text, setText, reset, rebase, conflict, dirty: !!draft && (text !== current || conflict) };
}
