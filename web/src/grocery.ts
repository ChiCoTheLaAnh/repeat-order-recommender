export type GroceryTrip = { id: string; date: string; items: string[] };
export type GrocerySuggestion = { item: string; reason: string };

export const demoPlannedDate = "2026-09-27";

// The completed trips from examples/grocery_synthetic.csv before demoPlannedDate.
export const groceryDemo: GroceryTrip[] = [
  { id: "trip-01", date: "2026-09-01", items: ["eggs", "oats"] },
  { id: "trip-02", date: "2026-09-07", items: ["oats", "eggs", "bananas"] },
  { id: "trip-03", date: "2026-09-17", items: ["eggs", "bananas"] },
  { id: "trip-04", date: "2026-09-25", items: ["oats", "milk", "spinach"] },
];

const dayMs = 86_400_000;
const itemKey = (item: string) => item.toLowerCase();
const alphabetical = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

export function validDate(value: string): boolean {
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value) || value.startsWith("0000")) {
    return false;
  }
  const time = Date.parse(value);
  return (
    Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value
  );
}

export function uniqueItems(items: string[]): string[] {
  const unique = new Map<string, string>();
  for (const value of items) {
    const name = value.trim().replace(/\s+/g, " ");
    const key = itemKey(name);
    if (name && !unique.has(key)) unique.set(key, name);
  }
  return [...unique.values()];
}

function median(values: number[]): number {
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2
    ? ordered[middle]
    : (ordered[middle - 1] + ordered[middle]) / 2;
}

const days = (value: number) => `${value} ${value === 1 ? "day" : "days"}`;

export function rankGroceries(
  trips: GroceryTrip[],
  plannedDate: string,
): GrocerySuggestion[] {
  const past = trips
    .filter((trip) => trip.date < plannedDate)
    .sort((a, b) => alphabetical(a.date, b.date) || alphabetical(a.id, b.id));
  const history = new Map<
    string,
    { name: string; count: number; dates: Set<string> }
  >();
  for (const trip of past) {
    for (const name of uniqueItems(trip.items)) {
      const key = itemKey(name);
      const item = history.get(key) ?? {
        name,
        count: 0,
        dates: new Set<string>(),
      };
      item.name = name;
      item.count++;
      item.dates.add(trip.date);
      history.set(key, item);
    }
  }

  const ranked = [];
  for (const [key, { name, count, dates }] of history) {
    const timestamps = [...dates].map((date) => Date.parse(date));
    const elapsed =
      (Date.parse(plannedDate) - timestamps[timestamps.length - 1]) / dayMs;
    const lastPurchase = `It’s been ${days(elapsed)} since your last purchase.`;
    let ratio = 0;
    let reason = `You bought ${name} on ${count} ${count === 1 ? "trip" : "trips"}. ${lastPurchase}`;
    if (timestamps.length >= 3) {
      const gap = median(
        timestamps
          .slice(1)
          .map((date, index) => (date - timestamps[index]) / dayMs),
      );
      if (elapsed < gap) continue;
      ratio = elapsed / gap;
      reason = `You usually buy ${name} every ${days(gap)}. ${lastPurchase}`;
    }
    ranked.push({ key, item: name, reason, ratio, count, elapsed });
  }
  // By design, due items have ratios of at least one and sparse items have zero.
  return ranked
    .sort(
      (a, b) =>
        b.ratio - a.ratio ||
        b.count - a.count ||
        a.elapsed - b.elapsed ||
        alphabetical(a.key, b.key),
    )
    .slice(0, 5)
    .map(({ item, reason }) => ({ item, reason }));
}
