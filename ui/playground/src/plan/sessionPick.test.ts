import { describe, expect, it } from "vitest";
import { PAGE, pageCount, pageOf, pageTicked, toggleOne, togglePage } from "./sessionPick";

const ids = Array.from({ length: 500 }, (_, i) => `s${i}`);

describe("the session picker's three layers", () => {
  it("cuts the sessions into pages of 50", () => {
    expect(PAGE).toBe(50);
    expect(pageCount(0)).toBe(1);
    expect(pageCount(50)).toBe(1);
    expect(pageCount(51)).toBe(2);
    expect(pageCount(ids.length)).toBe(10);
    expect(pageOf(ids, 2)).toEqual(ids.slice(100, 150));
    expect(pageOf(ids, 99)).toEqual(ids.slice(450)); // past the end: the last page
    expect(pageOf(ids.slice(0, 7), 0)).toHaveLength(7);
  });

  it("select all, untick page 3, tick one of it again: 500, 450, 451", () => {
    let value: string[] = []; // none named: every session, 500 of them
    const page3 = pageOf(ids, 2);
    expect(pageTicked(value, page3, true)).toBe(true);

    value = togglePage(value, ids, page3, true);
    expect(value).toHaveLength(450);
    expect(value).not.toContain("s100");
    expect(pageTicked(value, page3, false)).toBe(false);
    expect(pageTicked(value, pageOf(ids, 0), false)).toBe(true);

    value = toggleOne(value, ids, "s120", false);
    expect(value).toHaveLength(451);
    expect(value).toContain("s120");
    expect(pageTicked(value, page3, false)).toBe(false); // one of fifty
  });

  it("ticks a page onto what is already named, once each", () => {
    const page1 = pageOf(ids, 0);
    let value = ["s3", "s499"];
    value = togglePage(value, ids, page1, false);
    expect(value).toHaveLength(51); // s3 is not named twice
    expect(pageTicked(value, page1, false)).toBe(true);
    value = togglePage(value, ids, page1, false);
    expect(value).toEqual(["s499"]); // unticked: only what was named elsewhere stays
  });

  it("unticks one session out of all, and an empty page is never ticked", () => {
    expect(toggleOne([], ids, "s7", true)).toHaveLength(499);
    expect(toggleOne(["s1", "s2"], ids, "s1", false)).toEqual(["s2"]);
    expect(pageTicked([], [], true)).toBe(false);
  });
});
