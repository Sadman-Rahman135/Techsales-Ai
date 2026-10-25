// `npm test`: reading order and repair numbers the way people type them (US-05, ADR-031).
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { recordNumberIn, REPAIR_STATUS } from "./orders.ts";

describe("recordNumberIn", () => {
  it("finds an order or repair number however it is typed", () => {
    assert.equal(recordNumberIn("Where is my order TC-100002?"), "TC-100002");
    assert.equal(recordNumberIn("is rp 100001 ready"), "RP-100001");
    assert.equal(recordNumberIn("tc100003 please"), "TC-100003");
  });

  it("ignores numbers that are part of something else", () => {
    assert.equal(recordNumberIn("my order"), "");
    assert.equal(recordNumberIn("ABCTC-1000021"), "");
  });
});

describe("repair statuses", () => {
  it("have words for every status", () => {
    assert.ok(Object.values(REPAIR_STATUS).every((s) => s.label.length > 0));
  });
});
