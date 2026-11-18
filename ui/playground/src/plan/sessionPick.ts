/**
 * Ticking sessions in the Log Source's picker, in three layers: all of a log source, the
 * sessions of one page, a single session. The plan names sessions as a list, where none
 * named means every session of the log source ("implicit"); these functions turn a tick
 * into the next list. They hold no state, so the picker and its tests share them.
 */

/** Sessions shown per page of the picker. */
export const PAGE = 50;

/** How many pages `count` sessions take; at least one. */
export function pageCount(count: number): number {
  return Math.max(1, Math.ceil(count / PAGE));
}

/** The rows of one page, counted from 0. A page past the end is the last one. */
export function pageOf<T>(rows: T[], page: number): T[] {
  const at = Math.min(Math.max(0, page), pageCount(rows.length) - 1);
  return rows.slice(at * PAGE, (at + 1) * PAGE);
}

/** Whether every session of the page is ticked. An empty page is not. */
export function pageTicked(value: string[], pageIds: string[], implicit: boolean): boolean {
  if (pageIds.length === 0) return false;
  if (implicit) return true;
  const chosen = new Set(value);
  return pageIds.every((id) => chosen.has(id));
}

/**
 * Tick or untick a whole page. `ids` are every session of the log source, `pageIds` the
 * ones on the page. Unticking a page while every session is read names all the others.
 */
export function togglePage(value: string[], ids: string[], pageIds: string[], implicit: boolean): string[] {
  const page = new Set(pageIds);
  if (pageTicked(value, pageIds, implicit)) return (implicit ? ids : value).filter((id) => !page.has(id));
  const chosen = new Set(value);
  return [...value, ...pageIds.filter((id) => !chosen.has(id))];
}

/** Tick or untick one session. */
export function toggleOne(value: string[], ids: string[], id: string, implicit: boolean): string[] {
  if (implicit) return ids.filter((x) => x !== id);
  return value.includes(id) ? value.filter((x) => x !== id) : [...value, id];
}
