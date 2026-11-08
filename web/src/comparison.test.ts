import { describe, expect, it } from "vitest";
import demo from "../public/data/demo.json";
import { parseDemo } from "./data";
import { compareWithPopularity } from "./comparison";
describe("historical popularity comparison", () => {
  it("uses exact upstream prefixes without reranking or manufacturing history", () => {
    const data = parseDemo(demo);
    const profile = data.profiles.find((p) => p.id === "demo-typical")!;
    const baseline = data.profiles.find(
      (p) => p.mode === "popularity_fallback",
    )!;
    const before = JSON.stringify(data);
    const result = compareWithPopularity(data, profile)!;
    expect(result.personalized).toEqual(profile.recommendations.slice(0, 5));
    expect(result.popular).toEqual(baseline.recommendations.slice(0, 5));
    expect(result.shared).toBe(0);
    expect(result.popular.every((i) => i.previouslyPurchased === null)).toBe(
      true,
    );
    expect(JSON.stringify(data)).toBe(before);
  });
  it("counts shared products rather than matching ranks and keeps the global list fixed", () => {
    const data = parseDemo(demo);
    const first = compareWithPopularity(data, data.profiles[0])!;
    const second = compareWithPopularity(data, data.profiles[1])!;
    expect(first.shared).toBe(1);
    expect(first.popular).toEqual(second.popular);
    expect(first.personalized).not.toEqual(second.personalized);
  });
  it("does not claim personalization for an unknown shopper", () => {
    const data = parseDemo(demo);
    expect(
      compareWithPopularity(
        data,
        data.profiles.find((p) => p.id === "demo-guest")!,
      ),
    ).toBeNull();
  });
});
