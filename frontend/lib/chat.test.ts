// `npm test`: Node's built-in runner with type stripping (no test dependency). Pure logic only; the screens
// themselves are checked in the browser (docs/demos/10-ui.md).
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  type AskResult,
  bffFileUrl,
  bffPreviewUrl,
  type Citation,
  createSseParser,
  customerCopy,
  pageLabel,
  publicFirst,
  retryAfterSeconds,
  segments,
  sourceCount,
  stripMarkers,
} from "./chat.ts";

function citation(position: number, audience: string, title = `Doc ${position}`): Citation {
  return {
    position,
    document_id: "01a15413-5e0e-731f-9540-572330df0ffa",
    version_id: "01a15413-5e11-77ac-8745-4163ee6a496a",
    title,
    page_start: position,
    page_end: position,
    quote: "q",
    audience,
    preview_url: null,
  };
}

describe("SSE parser", () => {
  it("parses events split across chunks and ignores comments and unknown events", () => {
    const feed = createSseParser();
    assert.deepEqual(feed('event: meta\ndata: {"request_id":"req-1","trace_id":"t"}\n\n: keep-alive\n\nevent: sta'), [
      { event: "meta", data: { request_id: "req-1", trace_id: "t" } },
    ]);
    assert.deepEqual(feed('tus\r\ndata: {"stage":"searching"}\r\n\r\nevent: ping\ndata: {}\n\n'), [
      { event: "status", data: { stage: "searching" } },
    ]);
    assert.deepEqual(feed("event: delta\ndata: not json\n\n"), []); // malformed: dropped, never thrown
    const final = feed('event: final\ndata: {"answer_status":"answered","answer":"Hi [1]"}\n\n');
    assert.equal(final[0]?.event, "final");
  });
});

describe("answer text", () => {
  it("splits sentences from their citation markers", () => {
    assert.deepEqual(segments("Turn it off. [1] Open the door. [1][2]"), [
      { text: "Turn it off.", refs: [1] },
      { text: " Open the door.", refs: [1, 2] },
    ]);
    assert.deepEqual(segments("No markers here."), [{ text: "No markers here.", refs: [] }]);
  });

  it("hides provisional [S1] markers", () => {
    assert.equal(stripMarkers("Turn it off. [S1] Open the door. [S2]"), "Turn it off. Open the door.");
  });

  it("labels pages and counts sources the content-guide way", () => {
    assert.equal(pageLabel({ page_start: 17, page_end: 18 }), "p. 17–18");
    assert.equal(pageLabel({ page_start: 2, page_end: 2 }), "p. 2");
    assert.equal(pageLabel({ page_start: null, page_end: null }), null);
    assert.equal(sourceCount(1), "1 source");
    assert.equal(sourceCount(2), "2 sources");
  });
});

describe("employee assistant", () => {
  const result: Pick<AskResult, "answer" | "citations"> = {
    answer: "Waive the fee under warranty. [2] Customers can return within 30 days. [1] Ask the lead. [1][2]",
    citations: [citation(1, "public", "Returns Policy"), citation(2, "employee", "Repair Desk")],
  };

  it("lists public sources first and keeps their numbers", () => {
    assert.deepEqual(
      publicFirst([citation(1, "employee"), citation(2, "public"), citation(3, "public")]).map((c) => c.position),
      [2, 3, 1],
    );
  });

  it("copies for customers only sentences backed solely by public sources", () => {
    const copied = customerCopy(result);
    assert.ok(copied);
    assert.match(copied, /^Customers can return within 30 days\. \[1\]/);
    assert.doesNotMatch(copied, /Waive|Ask the lead|Repair Desk/);
    assert.match(copied, /\[1\] Returns Policy, p\. 1/);
  });

  it("copies nothing when no sentence is public", () => {
    assert.equal(customerCopy({ answer: "Internal only. [2] Unmarked.", citations: result.citations }), null);
  });
});

describe("plumbing", () => {
  it("reads the wait from a 429", () => {
    assert.equal(retryAfterSeconds({ code: "RATE_LIMITED", message: "", request_id: "r", details: { retry_after_seconds: 42 } }, null), 42);
    assert.equal(retryAfterSeconds(null, "17"), 17);
    assert.equal(retryAfterSeconds(null, null), 30);
  });

  it("maps API links to BFF routes and refuses anything else", () => {
    assert.equal(bffFileUrl("/api/v1/files/abc.def#page=2"), "/api/files/abc.def#page=2");
    const id = "01a15413-5e0e-731f-9540-572330df0ffa";
    assert.equal(
      bffPreviewUrl(`/api/v1/documents/${id}/preview?version_id=x&page=2`),
      `/api/documents/${id}/preview?version_id=x&page=2`,
    );
    assert.equal(bffPreviewUrl("https://evil.example/api/v1/documents/x/preview"), null);
    assert.equal(bffPreviewUrl(`/api/v1/documents/${id}/../../admin`), null);
  });
});
