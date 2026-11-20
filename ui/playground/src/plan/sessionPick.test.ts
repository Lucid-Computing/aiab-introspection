import { describe, expect, it } from "vitest";
import { PAGE, allTicked, fits, pageCount, pageOf, sizeText, tick, toggleMany, toggleOne, total } from "./sessionPick";

const ids = Array.from({ length: 500 }, (_, i) => `s${i}`);
const MB = 1 << 20;
const one = () => MB; // every session 1 MB

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

  it("starts with nothing ticked: none named is none, not all", () => {
    const value: string[] = [];
    expect(allTicked(value, ids)).toBe(false);
    expect(allTicked(value, pageOf(ids, 0))).toBe(false);
    expect(allTicked(value, [])).toBe(false);
    expect(total(value, one)).toBe(0);
  });

  it("without a limit: select all, untick page 3, tick one of it again: 500, 450, 451", () => {
    let { value } = toggleMany([], ids, one, null);
    expect(value).toHaveLength(500);
    const page3 = pageOf(ids, 2);
    ({ value } = toggleMany(value, page3, one, null));
    expect(value).toHaveLength(450);
    expect(value).not.toContain("s100");
    expect(allTicked(value, page3)).toBe(false);
    expect(allTicked(value, pageOf(ids, 0))).toBe(true);
    ({ value } = toggleOne(value, "s120", one, null));
    expect(value).toHaveLength(451);
    ({ value } = toggleOne(value, "s120", one, null));
    expect(value).toHaveLength(450);
  });

  it("takes no tick that would pass the limit, and says how many it left", () => {
    const limit = 50 * MB;
    // Select all of 500 sessions of 1 MB: the first 50 fit, 450 are left.
    const all = toggleMany([], ids, one, limit);
    expect(all.value).toEqual(ids.slice(0, 50));
    expect(all.left).toBe(450);
    expect(total(all.value, one)).toBe(limit);
    // One more does not fit, and the list stays as it was.
    expect(fits(all.value, "s60", one, limit)).toBe(false);
    expect(toggleOne(all.value, "s60", one, limit)).toEqual({ value: all.value, left: 1 });
    // Unticking always works, and makes room for another.
    const fewer = toggleOne(all.value, "s0", one, limit).value;
    expect(fewer).toHaveLength(49);
    expect(toggleOne(fewer, "s60", one, limit)).toEqual({ value: [...fewer, "s60"], left: 0 });
  });

  it("skips a session that is too large and still ticks the smaller ones after it", () => {
    const size = (id: string) => (id === "big" ? 40 * MB : 4 * MB);
    const got = tick(["a"], ["big", "b", "c"], size, 12 * MB);
    expect(got).toEqual({ value: ["a", "b", "c"], left: 1 });
  });

  it("counts a session of unknown size as nothing", () => {
    expect(total(["gone", "s1"], (id) => (id === "s1" ? MB : 0))).toBe(MB);
  });

  it("writes sizes in KB and MB", () => {
    expect(sizeText(0)).toBe("0 KB");
    expect(sizeText(300)).toBe("1 KB");
    expect(sizeText(320 * 1024)).toBe("320 KB");
    expect(sizeText(1.4 * MB)).toBe("1.4 MB");
  });
});
