import { describe, expect, it } from "vitest";
import { accuracy, countsText, evidenceText, labelTone, percent, riskTone, runLabel, sampleText, scoreText, sizeText, statusTone } from "./bench";

describe("bench formatting", () => {
  it("never shows a number for an unverified dimension, and keeps two decimals only when needed", () => {
    expect(scoreText({ score: null })).toBe("no score");
    expect(scoreText({ score: 10 })).toBe("10.0 / 10");
    expect(scoreText({ score: 9 })).toBe("9.0 / 10");
    expect(scoreText({ score: 9.82 })).toBe("9.82 / 10");
  });

  it("states the sample size next to every score", () => {
    expect(sampleText({ passed: 32, executed: 32, deferred: 5, cases: 37 })).toBe("32/32 executed passed · 5 deferred · 37 cases");
    expect(sampleText({ passed: 1, executed: 1, deferred: 0, cases: 1 })).toBe("1/1 executed passed · 1 case");
  });

  it("maps labels, statuses and policy levels to tones", () => {
    expect(labelTone("verified")).toBe("done");
    expect(labelTone("provisional")).toBe("wait");
    expect(labelTone("unverified")).toBe("err");
    expect(statusTone("pass")).toBe("done");
    expect(statusTone("error")).toBe("err");
    expect(statusTone("deferred")).toBe("wait");
    expect(statusTone("not run")).toBe("idle");
    expect(riskTone("YELLOW_AUTO")).toBe("done");
    expect(riskTone("YELLOW_APPROVAL")).toBe("wait");
    expect(riskTone("DENY")).toBe("err");
    expect(riskTone(null)).toBe("idle");
  });

  it("lists evidence kinds that are present, integration first", () => {
    expect(evidenceText({ integration: 40, mocked: 11, live: 0 })).toBe("40 integration · 11 mocked");
    expect(evidenceText({ live: 3 })).toBe("3 live");
    expect(evidenceText({})).toBe("no cases");
  });

  it("formats counts, progress, sizes, run names and accuracy", () => {
    expect(countsText({ pass: 548, fail: 0, error: 0, deferred: 24 })).toBe("548 passed · 0 failed · 0 errors · 24 deferred");
    expect(countsText({ pass: 12, fail: 0 })).toBe("12 passed · 0 failed");
    expect(countsText(null)).toBe("no counts");
    expect(percent(3, 12)).toBe(25);
    expect(percent(5, 0)).toBe(0);
    expect(percent(20, 10)).toBe(100);
    expect(sizeText(2497281120)).toBe("2.50 GB");
    expect(sizeText(730895168)).toBe("731 MB");
    expect(sizeText(0)).toBe("size unknown");
    expect(runLabel("bench-20261017-031703.json")).toBe("17 Oct 03:17 · full run");
    expect(runLabel("bench-20261016-181824-K.json")).toBe("16 Oct 18:18 · area K");
    expect(runLabel("bench-20261016-181824-sel.json")).toBe("16 Oct 18:18 · selected cases");
    expect(runLabel("other.json")).toBe("other.json");
    expect(accuracy({ model: "m", correct: 63, total: 70 })).toBe("63/70 (90%)");
    expect(accuracy({ model: "m", error: "boom" })).toBe("error");
    expect(accuracy({ model: "m" })).toBe("-");
  });
});
