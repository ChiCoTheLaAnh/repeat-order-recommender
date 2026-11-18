import { describe, expect, it } from "vitest";
import { groceryDemo, rankGroceries, uniqueItems } from "./grocery";

describe("grocery ranking", () => {
  it("matches the Python demo and skips an item whose next purchase is still early", () => {
    const suggestions = rankGroceries(groceryDemo, "2026-09-27");
    expect(suggestions.map(({ item }) => item)).toEqual([
      "eggs",
      "bananas",
      "milk",
      "spinach",
    ]);
    expect(suggestions[0].reason).toBe(
      "You usually buy eggs every 8 days. It’s been 10 days since your last purchase.",
    );
  });

  it("counts a duplicate once and never uses a trip on or after the planned date", () => {
    const history = [
      { id: "1", date: "2026-09-17", items: ["eggs", " EGGS ", "oats"] },
      { id: "2", date: "2026-09-25", items: ["oats"] },
    ];
    const later = [
      { id: "3", date: "2026-09-27", items: ["eggs", "chicken"] },
      { id: "4", date: "2026-09-28", items: ["milk"] },
    ];
    const expected = rankGroceries(history, "2026-09-27");
    expect(expected.map(({ item }) => item)).toEqual(["oats", "eggs"]);
    expect(expected[1].reason).toContain("1 trip.");
    expect(rankGroceries([...history, ...later], "2026-09-27")).toEqual(
      expected,
    );
  });

  it("keeps first spelling and normalizes spaces within a trip", () => {
    expect(
      uniqueItems(["Greek  yogurt", " greek yogurt ", "", "Rice"]),
    ).toEqual(["Greek yogurt", "Rice"]);
  });
});
