// `npm test`: export links and review labels (ADR-030).
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { EXPORTS, exportUrl, REVIEW_REASON, REVIEW_STATUS } from "./reviews.ts";

describe("exportUrl", () => {
  it("goes through the BFF with the dashboard's date range", () => {
    assert.equal(exportUrl("questions", "2026-10-01", "2026-10-07"), "/api/admin/exports/questions?start=2026-10-01&end=2026-10-07");
  });

  it("leaves the range to the API when none is given", () => {
    assert.equal(exportUrl("documents"), "/api/admin/exports/documents");
  });

  it("offers every dataset the API exports, and nothing else", () => {
    assert.deepEqual(
      EXPORTS.map((e) => e.dataset).sort(),
      ["documents", "feedback", "questions", "reviews", "tickets"],
    );
  });
});

describe("review labels", () => {
  it("names every status and reason in words", () => {
    assert.deepEqual(Object.keys(REVIEW_STATUS), ["open", "resolved", "dismissed"]);
    assert.ok(Object.values(REVIEW_REASON).every((label) => label.length > 0));
  });
});
