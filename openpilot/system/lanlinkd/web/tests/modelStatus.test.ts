import { describe, expect, it } from "vitest";
import { replyTimelinessText } from "../src/lib/modelStatus";

describe("replyTimelinessText", () => {
  it("shows on-time / late / missing counts with the on-time rate", () => {
    expect(replyTimelinessText({ replyOnTime: 30, replyLate: 15, replyMissing: 5 })).toBe(
      "按时 30 · 迟到 15 · 没回 5（按时率约 60%）",
    );
  });

  it("shows a dash before any frame or on an old snapshot without counts", () => {
    expect(replyTimelinessText({ replyOnTime: 0, replyLate: 0, replyMissing: 0 })).toBe("—");
    expect(replyTimelinessText(null)).toBe("—");
  });
});
