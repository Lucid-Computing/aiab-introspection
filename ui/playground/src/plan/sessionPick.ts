/**
 * Ticking sessions in the Log Source's picker, in three layers: all of a log source, the
 * sessions of one page, a single session. The plan names sessions as a list and a run sends
 * the ones named: none named, none sent. A run can send only so much (the limit, in bytes),
 * so a tick that would pass it is not taken. These functions turn a tick into the next
 * list. They hold no state, so the picker and its tests share them.
 */

/** Sessions shown per page of the picker. */
export const PAGE = 50;

/** How large a session is, in bytes; 0 when it is not known. */
export type SizeOf = (id: string) => number;

/** The next list, and how many sessions were left unticked because they did not fit. */
export interface Ticked {
  value: string[];
  left: number;
}

/** How many pages `count` sessions take; at least one. */
export function pageCount(count: number): number {
  return Math.max(1, Math.ceil(count / PAGE));
}

/** The rows of one page, counted from 0. A page past the end is the last one. */
export function pageOf<T>(rows: T[], page: number): T[] {
  const at = Math.min(Math.max(0, page), pageCount(rows.length) - 1);
  return rows.slice(at * PAGE, (at + 1) * PAGE);
}

/** The bytes a run sends for the sessions named. */
export function total(value: string[], size: SizeOf): number {
  return value.reduce((sum, id) => sum + size(id), 0);
}

/** Whether one more session fits under the limit; no limit, it always does. */
export function fits(value: string[], id: string, size: SizeOf, limit: number | null): boolean {
  return limit === null || total(value, size) + size(id) <= limit;
}

/** Whether every one of `ids` is ticked. An empty list is not. */
export function allTicked(value: string[], ids: string[]): boolean {
  if (ids.length === 0) return false;
  const chosen = new Set(value);
  return ids.every((id) => chosen.has(id));
}

/** Tick `ids` in order, each only while the total stays under the limit. */
export function tick(value: string[], ids: string[], size: SizeOf, limit: number | null): Ticked {
  const chosen = new Set(value);
  const next = [...value];
  let sum = total(value, size);
  let left = 0;
  for (const id of ids) {
    if (chosen.has(id)) continue;
    if (limit !== null && sum + size(id) > limit) {
      left += 1;
      continue;
    }
    next.push(id);
    sum += size(id);
  }
  return { value: next, left };
}

/** Tick or untick a group (a page, or all of a log source): untick when all of it is ticked. */
export function toggleMany(value: string[], ids: string[], size: SizeOf, limit: number | null): Ticked {
  if (!allTicked(value, ids)) return tick(value, ids, size, limit);
  const group = new Set(ids);
  return { value: value.filter((id) => !group.has(id)), left: 0 };
}

/** Tick or untick one session. */
export function toggleOne(value: string[], id: string, size: SizeOf, limit: number | null): Ticked {
  if (value.includes(id)) return { value: value.filter((x) => x !== id), left: 0 };
  return tick(value, [id], size, limit);
}

/** "1.4 MB", "320 KB": a size as the picker shows it. */
export function sizeText(bytes: number): string {
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} MB`;
  return `${Math.max(bytes ? 1 : 0, Math.round(bytes / 1024))} KB`;
}
