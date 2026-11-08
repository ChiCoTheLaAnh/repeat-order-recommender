import { topItems, type Demo, type Profile } from "./data";
/** Display-only comparison of existing release results. No evaluation or scoring. */
export function compareWithPopularity(data: Demo, profile: Profile) {
  if (profile.mode === "popularity_fallback") return null;
  const fallback = data.profiles.find((p) => p.mode === "popularity_fallback");
  if (!fallback) throw Error("The approved popularity example is missing");
  const personalized = topItems(profile, 5),
    popular = topItems(fallback, 5);
  const popularSkus = new Set(popular.map((i) => i.sku));
  return {
    personalized,
    popular,
    shared: personalized.filter((i) => popularSkus.has(i.sku)).length,
  };
}
