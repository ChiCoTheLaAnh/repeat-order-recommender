import { describe, expect, it } from "vitest";
import demo from "../public/data/demo.json";
import { parseDemo, topItems, recommendationMix, displayDate } from "./data";

describe("frozen public demo contract", () => {
  it("loads five anonymous profiles with twenty finite, unique, contiguous results each", () => {
    const data = parseDemo(demo);
    expect(data.defaultProfile).toBe("demo-typical");
    expect(data.profiles).toHaveLength(5);
    for (const profile of data.profiles) {
      expect(profile.recommendations).toHaveLength(20);
      expect(new Set(profile.recommendations.map((i) => i.sku)).size).toBe(20);
      expect(profile.recommendations.map((i) => i.rank)).toEqual(
        Array.from({ length: 20 }, (_, i) => i + 1),
      );
      expect(topItems(profile, 5)).toEqual(topItems(profile, 20).slice(0, 5));
      if (profile.mode === "personalized")
        expect(
          recommendationMix(profile, 10)!.repeat +
            recommendationMix(profile, 10)!.fresh,
        ).toBe(10);
    }
    expect(displayDate(data.metadata.dataAsOf)).toBe("May 1, 2011");
  });
  it("never treats a guest as a customer with zero purchases or all-new recommendations", () => {
    const guest = parseDemo(demo).profiles.find(
      (p) => p.mode === "popularity_fallback",
    )!;
    expect(guest.summary).toBeNull();
    expect(guest.monthly).toEqual([]);
    expect(recommendationMix(guest, 10)).toBeNull();
    expect(
      guest.recommendations.every((i) => i.previouslyPurchased === null),
    ).toBe(true);
  });
  it("rejects wrong schema, missing ranks, duplicates, future cutoff and private fields", () => {
    for (const change of [
      (x: any) => {
        x.metadata.customer_id = "private";
      },
      (x: any) => {
        x.profiles[0].monthly[0].invoice_id = "private";
      },
      (x: any) => {
        x.profiles[4].summary = {
          invoiceCount: 0,
          skuCount: 0,
          daysSinceLastPurchase: 0,
        };
      },
      (x: any) => {
        x.profiles[0].recommendations[0].previouslyPurchased = null;
      },
      (x: any) => {
        x.schemaVersion = "future";
      },
      (x: any) => {
        x.profiles[0].recommendations[0].rank = 99;
      },
      (x: any) => {
        x.profiles[0].recommendations[1].sku =
          x.profiles[0].recommendations[0].sku;
      },
      (x: any) => {
        x.metadata.dataAsOf = "2011-06-01T00:00:00";
      },
      (x: any) => {
        x.profiles[0].customer_id = "private";
      },
    ]) {
      const altered = structuredClone(demo);
      change(altered);
      expect(() => parseDemo(altered)).toThrow();
    }
  });
});
