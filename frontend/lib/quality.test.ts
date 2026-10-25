import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { addDays, change, daysBetween, percent, previousRange, resolveRange, shortDay } from "./quality.ts";

describe("date ranges", () => {
  it("resolves presets against the store's today", () => {
    assert.deepEqual(resolveRange({}, "2026-10-19"), { start: "2026-10-13", end: "2026-10-19", preset: "7d" });
    assert.deepEqual(resolveRange({ range: "today" }, "2026-10-19"), {
      start: "2026-10-19",
      end: "2026-10-19",
      preset: "today",
    });
    assert.deepEqual(resolveRange({ range: "30d" }, "2026-03-01"), { start: "2026-01-31", end: "2026-03-01", preset: "30d" });
  });

  it("accepts custom ranges, swaps reversed ones, and ignores junk", () => {
    assert.deepEqual(resolveRange({ range: "custom", start: "2026-09-30", end: "2026-09-01" }, "2026-10-19"), {
      start: "2026-09-01",
      end: "2026-09-30",
      preset: "custom",
    });
    assert.equal(resolveRange({ range: "custom", start: "yesterday", end: "2026-09-01" }, "2026-10-19").preset, "7d");
  });

  it("finds the previous period of the same length across month and DST boundaries", () => {
    assert.deepEqual(previousRange({ start: "2026-11-01", end: "2026-11-07" }), { start: "2026-10-25", end: "2026-10-31" });
    assert.equal(daysBetween("2026-03-01", "2026-03-31"), 31);
    assert.equal(addDays("2026-12-31", 1), "2027-01-01");
    assert.equal(shortDay("2026-10-13"), "13 Oct");
  });
});

describe("KPI changes", () => {
  it("rounds shares and refuses to divide by zero", () => {
    assert.equal(percent(90, 146), 62);
    assert.equal(percent(0, 0), null);
  });

  it("states the change with a sign, never by color alone", () => {
    assert.deepEqual(change(146, 134, "count"), { direction: "up", text: "+12" });
    assert.deepEqual(change(58, 62, "points"), { direction: "down", text: "−4 pts" });
    assert.deepEqual(change(5, 5, "count"), { direction: "same", text: "No change" });
    assert.equal(change(5, null, "count"), null);
  });
});
