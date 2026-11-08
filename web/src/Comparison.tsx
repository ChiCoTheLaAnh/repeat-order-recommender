import type { Demo, Profile } from "./data";
import { compareWithPopularity } from "./comparison";
export function Comparison({
  data,
  profile,
}: {
  data: Demo;
  profile: Profile;
}) {
  const result = compareWithPopularity(data, profile);
  if (!result) return null;
  return (
    <section
      className="shopping-comparison"
      aria-label="Bestseller and personalized comparison"
    >
      <details open>
        <summary>
          <div>
            <span className="section-label">
              THE SAME SHOPPER. TWO APPROACHES.
            </span>
            <h3>What changes when we use their history?</h3>
          </div>
          <span aria-hidden="true">+</span>
        </summary>
        <p className="comparison-intro">
          Compare the first 5 picks: a bestseller shelf stays the same for
          everyone, while a personalized shelf uses this shopper’s past
          purchases. The Top 5/10/20 control applies to the detailed cards
          below.
        </p>
        <div className="shelves">
          <section aria-label="Bestsellers for everyone">
            <div className="shelf-heading">
              <span>01 / POPULARITY</span>
              <h4>Popular with everyone</h4>
              <p>Same list across all four profiles</p>
            </div>
            <ol>
              {result.popular.map((item) => (
                <li key={item.sku}>
                  <span className="shelf-rank">{item.rank}</span>
                  <div>
                    <strong>{item.name.toLowerCase()}</strong>
                    <small>SKU {item.sku}</small>
                  </div>
                </li>
              ))}
            </ol>
          </section>
          <section
            className="personal-shelf"
            aria-label="Personalized for selected shopper"
          >
            <div className="shelf-heading">
              <span>02 / PERSONALIZED</span>
              <h4>Picked for this shopper</h4>
              <p>Frozen model · {profile.label}</p>
            </div>
            <ol>
              {result.personalized.map((item) => (
                <li key={item.sku}>
                  <span className="shelf-rank">{item.rank}</span>
                  <div>
                    <strong>{item.name.toLowerCase()}</strong>
                    <small>
                      SKU {item.sku}{" "}
                      <span className="shelf-history">
                        {item.previouslyPurchased
                          ? "Bought before"
                          : "New to this shopper"}
                      </span>
                    </small>
                  </div>
                </li>
              ))}
            </ol>
          </section>
        </div>
        <p className="shelf-takeaway">
          <strong>{result.shared} of 5 products appear in both lists.</strong>{" "}
          Switch shoppers to see what changes. This compares product choices,
          not purchase outcomes.
        </p>
        <p className="comparison-limit">
          Bestsellers use distinct identified purchasers in the past 30 days.
          Both lists use only pre-cutoff data. Different recommendations do not
          prove better results or increased sales.
        </p>
      </details>
    </section>
  );
}
