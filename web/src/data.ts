export interface Item {
  rank: number;
  sku: string;
  name: string;
  score: number;
  scoreKind: string;
  previouslyPurchased: boolean | null;
  purchaseInvoiceCount: number | null;
  daysSinceLastPurchase: number | null;
  sources: string[];
}
export interface Profile {
  id: string;
  label: string;
  subtitle: string;
  mode: string;
  summary: {
    invoiceCount: number;
    skuCount: number;
    daysSinceLastPurchase: number;
  } | null;
  monthly: { month: string; label: string; invoiceCount: number }[];
  recommendations: Item[];
}
export interface Demo {
  schemaVersion: string;
  defaultProfile: string;
  metadata: {
    dataAsOf: string;
    windowStart: string;
    windowDays: number;
    releaseId: string;
    modelVersion: string;
    artifactSourceCommit: string;
    inferenceMode: string;
    repository: string;
  };
  quality: {
    heuristicNdcg: number;
    logisticNdcg: number;
    xgbNdcg: number;
    primaryQueries: number;
    primaryQueryFraction: number;
    description: string;
  };
  profiles: Profile[];
}
export function parseDemo(value: unknown): Demo {
  const d = value as Demo;
  if (
    !d ||
    d.schemaVersion !== "public_demo_v1" ||
    d.metadata?.dataAsOf !== "2011-05-01T00:00:00" ||
    d.profiles?.length !== 5
  )
    throw Error("Unsupported historical demo data");
  const allowed: Record<string, string[]> = {
    root: [
      "schemaVersion",
      "defaultProfile",
      "metadata",
      "quality",
      "profiles",
    ],
    metadata: [
      "dataAsOf",
      "windowStart",
      "windowDays",
      "releaseId",
      "modelVersion",
      "artifactSourceCommit",
      "inferenceMode",
      "repository",
    ],
    quality: [
      "heuristicNdcg",
      "logisticNdcg",
      "xgbNdcg",
      "primaryQueries",
      "primaryQueryFraction",
      "description",
    ],
    summary: ["invoiceCount", "skuCount", "daysSinceLastPurchase"],
    month: ["month", "label", "invoiceCount"],
    profile: [
      "id",
      "label",
      "subtitle",
      "mode",
      "summary",
      "monthly",
      "recommendations",
    ],
    item: [
      "rank",
      "sku",
      "name",
      "score",
      "scoreKind",
      "previouslyPurchased",
      "purchaseInvoiceCount",
      "daysSinceLastPurchase",
      "sources",
    ],
  };
  function keys(obj: object, kind: string) {
    if (Object.keys(obj).sort().join() !== allowed[kind].sort().join())
      throw Error("Unexpected demo fields");
  }
  keys(d, "root");
  keys(d.metadata, "metadata");
  keys(d.quality, "quality");
  if (
    d.defaultProfile !== "demo-typical" ||
    d.metadata.windowDays !== 180 ||
    d.metadata.windowStart !== "2010-11-02T00:00:00" ||
    d.metadata.inferenceMode !== "precomputed" ||
    d.metadata.repository !==
      "https://github.com/ChiCoTheLaAnh/repeat-order-recommender"
  )
    throw Error("Unsupported demo policy");
  const expected = new Set([
    "demo-light",
    "demo-typical",
    "demo-frequent",
    "demo-less-recent",
    "demo-guest",
  ]);
  const count = (v: number) => Number.isInteger(v) && v >= 0;

  for (const p of d.profiles) {
    keys(p, "profile");
    if (!expected.has(p.id) || p.recommendations.length !== 20)
      throw Error("Invalid profile");
    const guest = p.id === "demo-guest";
    if (p.mode !== (guest ? "popularity_fallback" : "personalized"))
      throw Error("Invalid mode");
    if (guest) {
      if (p.summary !== null || p.monthly.length !== 0)
        throw Error("Guest history must be absent");
    } else {
      if (!p.summary || p.monthly.length !== 6) throw Error("Missing history");
      keys(p.summary, "summary");
      if (
        !count(p.summary.invoiceCount) ||
        !count(p.summary.skuCount) ||
        !Number.isFinite(p.summary.daysSinceLastPurchase) ||
        p.summary.daysSinceLastPurchase < 0 ||
        p.summary.daysSinceLastPurchase > 180
      )
        throw Error("Invalid history");
      const months = [
        "2010-11",
        "2010-12",
        "2011-01",
        "2011-02",
        "2011-03",
        "2011-04",
      ];
      p.monthly.forEach((m, n) => {
        keys(m, "month");
        if (m.month !== months[n] || !count(m.invoiceCount))
          throw Error("Invalid activity");
      });
    }
    const seen = new Set<string>();
    p.recommendations.forEach((i, n) => {
      keys(i, "item");
      if (i.rank !== n + 1 || seen.has(i.sku) || !Number.isFinite(i.score))
        throw Error("Invalid recommendations");
      if (
        typeof i.sku !== "string" ||
        !i.sku ||
        typeof i.name !== "string" ||
        !Array.isArray(i.sources) ||
        i.sources.some(
          (s) =>
            ![
              "history",
              "neighbors",
              "recent_popularity",
              "long_popularity",
            ].includes(s),
        )
      )
        throw Error("Invalid product");
      if (
        guest
          ? i.previouslyPurchased !== null ||
            i.purchaseInvoiceCount !== null ||
            i.daysSinceLastPurchase !== null ||
            i.scoreKind !== "distinct_identified_purchasers_30d"
          : typeof i.previouslyPurchased !== "boolean" ||
            !count(i.purchaseInvoiceCount!) ||
            i.scoreKind !== "raw_model_margin" ||
            (i.daysSinceLastPurchase !== null &&
              (!Number.isFinite(i.daysSinceLastPurchase) ||
                i.daysSinceLastPurchase < 0))
      )
        throw Error("Invalid historical signals");
      seen.add(i.sku);
    });
  }
  if (
    new Set(d.profiles.map((p) => p.id)).size !== 5 ||
    !d.profiles.some((p) => p.id === d.defaultProfile)
  )
    throw Error("Invalid selection");
  return d;
}
export const topItems = (p: Profile, k: number) =>
  p.recommendations.slice(0, k);
export const recommendationMix = (p: Profile, k: number) =>
  p.mode === "popularity_fallback"
    ? null
    : {
        repeat: topItems(p, k).filter((i) => i.previouslyPurchased).length,
        fresh: topItems(p, k).filter((i) => i.previouslyPurchased === false)
          .length,
      };
export const displayDate = (s: string) =>
  new Intl.DateTimeFormat("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(s.slice(0, 10) + "T00:00:00Z"));
