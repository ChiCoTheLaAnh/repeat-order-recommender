import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  parseDemo,
  topItems,
  recommendationMix,
  displayDate,
  type Demo,
  type Profile,
  type Item,
} from "./data";
import "./style.css";
function Activity({ profile }: { profile: Profile }) {
  const [tip, setTip] = useState("");
  const max = Math.max(1, ...profile.monthly.map((m) => m.invoiceCount));
  return (
    <section className="activity">
      <div className="section-label">
        PURCHASE ACTIVITY <span>Distinct invoices · 180 days</span>
      </div>
      <svg
        viewBox="0 0 420 150"
        role="img"
        aria-label="Monthly distinct purchase invoices"
      >
        <title>
          Purchase activity, November 2, 2010 through April 30, 2011
        </title>
        {[0, 1, 2].map((n) => (
          <line
            key={n}
            x1="20"
            x2="405"
            y1={20 + n * 46}
            y2={20 + n * 46}
            stroke="#e6eaf0"
          />
        ))}
        {profile.monthly.map((m, i) => {
          const height = (m.invoiceCount / max) * 90;
          return (
            <g key={m.month}>
              <rect
                tabIndex={0}
                aria-label={`${m.label}: ${m.invoiceCount} invoices`}
                x={30 + i * 64}
                y={112 - height}
                width="36"
                height={Math.max(height, 2)}
                rx="5"
                onFocus={() => setTip(`${m.label}: ${m.invoiceCount} invoices`)}
                onBlur={() => setTip("")}
                onMouseEnter={() =>
                  setTip(`${m.label}: ${m.invoiceCount} invoices`)
                }
                onMouseLeave={() => setTip("")}
              />
              <text
                x={48 + i * 64}
                y="104"
                dy={-height}
                textAnchor="middle"
                className="chart-count"
              >
                {m.invoiceCount}
              </text>
              <text x={48 + i * 64} y="138" textAnchor="middle">
                {m.label.slice(0, 3)}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="chart-note" aria-live="polite">
        {tip || "Nov 2, 2010 – Apr 30, 2011 · November is a partial month"}
      </div>
      <details>
        <summary>View activity data</summary>
        <table>
          <thead>
            <tr>
              <th>Month</th>
              <th>Distinct invoices</th>
            </tr>
          </thead>
          <tbody>
            {profile.monthly.map((m) => (
              <tr key={m.month}>
                <td>{m.label}</td>
                <td>{m.invoiceCount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}
function Product({ item, guest }: { item: Item; guest: boolean }) {
  return (
    <article className="product">
      <div className="product-top">
        <span className="rank">{String(item.rank).padStart(2, "0")}</span>
        <span
          className={`badge ${item.previouslyPurchased ? "repeat" : "new"}`}
        >
          {guest
            ? "Popular pick"
            : item.previouslyPurchased
              ? "Previously purchased"
              : "New to this customer"}
        </span>
      </div>
      <h3>{item.name.toLowerCase()}</h3>
      <div className="sku">SKU {item.sku}</div>
      <details>
        <summary>
          View historical signals <span aria-hidden="true">+</span>
        </summary>
        <div className="signals">
          {!guest && (
            <>
              <p>
                Past purchase invoices{" "}
                <strong>{item.purchaseInvoiceCount ?? 0}</strong>
              </p>
              <p>
                Days since item purchase{" "}
                <strong>
                  {item.daysSinceLastPurchase === null
                    ? "Never"
                    : Math.floor(item.daysSinceLastPurchase)}
                </strong>
              </p>
            </>
          )}
          <p>
            Source signals{" "}
            <strong>
              {item.sources.map((s) => s.replaceAll("_", " ")).join(", ") ||
                "recent popularity"}
            </strong>
          </p>
          <p>
            {guest ? "30-day identified purchasers" : "Ranking score"}{" "}
            <strong>{item.score.toFixed(guest ? 0 : 4)}</strong>
          </p>
          <small>
            {guest
              ? "Distinct identified purchasers, not a personal score."
              : "Raw model margin, not a purchase probability. Historical signals are not causal explanations."}{" "}
            Item purchase history uses all available past data.
          </small>
        </div>
      </details>
    </article>
  );
}
function App() {
  const [data, setData] = useState<Demo | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState("demo-typical");
  const [k, setK] = useState(10);
  useEffect(() => {
    const c = new AbortController();
    setError(false);
    fetch(import.meta.env.BASE_URL + "data/demo.json", { signal: c.signal })
      .then((r) => {
        if (!r.ok) throw Error("Data unavailable");
        return r.json();
      })
      .then(parseDemo)
      .then(setData)
      .catch((e) => {
        if (e.name !== "AbortError") setError(true);
      });
    return () => c.abort();
  }, [attempt]);
  if (!data)
    return (
      <main className="load" role="status">
        {error ? (
          <>
            <h1>The demo couldn’t load.</h1>
            <p>Please check your connection and try again.</p>
            <button onClick={() => setAttempt(attempt + 1)}>Try again</button>
          </>
        ) : (
          "Loading historical examples…"
        )}
      </main>
    );
  const p = data.profiles.find((p) => p.id === selected)!;
  const guest = p.mode === "popularity_fallback";
  const mix = recommendationMix(p, k);
  return (
    <>
      <header>
        <a className="wordmark" href="#">
          re<span>/</span>order<span className="wordmark-dot">.</span>
        </a>
        <nav>
          <a href="#how-it-works">How it works</a>
          <a href={data.metadata.repository}>GitHub </a>
        </nav>
      </header>
      <main>
        <section className="hero">
          <div className="eyebrow">
            <span className="status-dot" /> HISTORICAL RECOMMENDATION DEMO
          </div>
          <h1>
            A familiar favourite.
            <br />
            <span>A little discovery.</span>
          </h1>
          <p>
            Explore what a repeat-first recommender would suggest next.
            <br className="desktop-break" /> Real retail history. A frozen
            model. Five ways to look inside.
          </p>
          <div className="date-note">
            Past-only data{" "}
            <strong>before {displayDate(data.metadata.dataAsOf)}</strong>
            <span>Precomputed examples · no sign-in</span>
          </div>
        </section>
        <div className="workspace">
          <aside>
            <div className="section-label">CHOOSE A SHOPPER</div>
            <div className="profiles" role="group" aria-label="Sample profiles">
              {data.profiles.map((profile, i) => (
                <button
                  className={
                    selected === profile.id ? "profile selected" : "profile"
                  }
                  key={profile.id}
                  aria-pressed={selected === profile.id}
                  onClick={() => setSelected(profile.id)}
                >
                  <span className="avatar">
                    {i === 4 ? "?" : String.fromCharCode(65 + i)}
                  </span>
                  <span>
                    <strong>{profile.label}</strong>
                    <small>{profile.subtitle}</small>
                  </span>
                  <span className="profile-arrow" aria-hidden="true">
                    ›
                  </span>
                </button>
              ))}
            </div>
            <p className="selection-note">
              Four anonymous examples selected using past invoice frequency and
              recency only. No future purchase outcomes were used.
            </p>
            <div className="history-panel">
              <h2>
                {guest ? "No personal history" : "A look at the last 180 days"}
              </h2>
              {p.summary ? (
                <>
                  <div className="stats">
                    <div>
                      <strong>{p.summary.invoiceCount}</strong>
                      <span>invoices</span>
                    </div>
                    <div>
                      <strong>{p.summary.skuCount}</strong>
                      <span>distinct SKUs</span>
                    </div>
                    <div>
                      <strong>
                        {Math.floor(p.summary.daysSinceLastPurchase)}
                        <small>d</small>
                      </strong>
                      <span>since purchase</span>
                    </div>
                  </div>
                  <Activity profile={p} />
                </>
              ) : (
                <p className="guest-note">
                  We don’t know this shopper. Recommendations fall back to
                  products with the most distinct identified purchasers in the
                  preceding 30 days. There is no personal history or repeat/new
                  classification.
                </p>
              )}
            </div>
          </aside>
          <section className="results" aria-label="Recommendations">
            <div className="results-heading">
              <div className="section-label">
                {guest ? "POPULARITY FALLBACK" : "PERSONALIZED SHORTLIST"}
              </div>
              <div className="result-title">
                <h2>
                  {guest ? "A popular place to start." : "What comes next?"}
                </h2>
                <div
                  className="topk"
                  role="group"
                  aria-label="Number of recommendations"
                >
                  {[5, 10, 20].map((n) => (
                    <button
                      key={n}
                      aria-pressed={k === n}
                      onClick={() => setK(n)}
                    >
                      Top {n}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            {mix ? (
              <div className="mix">
                <svg
                  viewBox="0 0 44 44"
                  role="img"
                  aria-label={`${mix.repeat} previously purchased, ${mix.fresh} new to this customer`}
                >
                  <circle
                    cx="22"
                    cy="22"
                    r="17"
                    fill="none"
                    stroke="#c8d9f5"
                    strokeWidth="7"
                  />
                  <circle
                    cx="22"
                    cy="22"
                    r="17"
                    fill="none"
                    stroke="#2856d8"
                    strokeWidth="7"
                    pathLength="100"
                    strokeDasharray={`${(mix.repeat / k) * 100} 100`}
                    transform="rotate(-90 22 22)"
                  />
                </svg>
                <div>
                  <strong>
                    {mix.repeat} familiar <span>·</span> {mix.fresh} new
                  </strong>
                  <p>
                    In this top {k}. “New” means never purchased by this shopper
                    before cutoff.
                  </p>
                </div>
              </div>
            ) : (
              <div className="mix guest-mix">
                <span></span>
                <div>
                  <strong>Popular with other shoppers</strong>
                  <p>A catalog-based fallback, without personalized scores.</p>
                </div>
              </div>
            )}
            <div className="products">
              {topItems(p, k).map((item) => (
                <Product key={p.id + item.sku} item={item} guest={guest} />
              ))}
            </div>
            <p className="result-footnote">
              Original retail descriptions · no live prices, inventory or
              product imagery.
              <br />
              Rankings are precomputed from the approved release, not generated
              in your browser.
            </p>
          </section>
        </div>
        <section id="how-it-works" className="how">
          <details>
            <summary>
              <div>
                <span className="section-label">BEHIND THE DEMO</span>
                <h2>A simple interface. A careful pipeline.</h2>
              </div>
              <span className="expand">+</span>
            </summary>
            <div className="how-content">
              <div className="pipeline">
                {[
                  "Audit & preserve raw data",
                  "Past-only monthly snapshots",
                  "Retrieve up to 200 candidates",
                  "Rank with logistic regression",
                ].map((s, i) => (
                  <div key={s}>
                    <span>0{i + 1}</span>
                    <strong>{s}</strong>
                  </div>
                ))}
              </div>
              <div className="technical-grid">
                <div>
                  <h3>Why this model?</h3>
                  <p>
                    Logistic regression is close to the tested XGBoost model on
                    validation, with a much smaller model artifact. Its scores
                    order products; they are not calibrated purchase
                    probabilities.
                  </p>
                  <table>
                    <caption>Validation NDCG@10 · higher is better</caption>
                    <tbody>
                      {[
                        ["Heuristic", data.quality.heuristicNdcg],
                        ["Logistic · selected", data.quality.logisticNdcg],
                        ["XGBoost", data.quality.xgbNdcg],
                      ].map(([s, v]) => (
                        <tr key={s}>
                          <th>{s}</th>
                          <td>{Number(v).toFixed(4)}</td>
                          <td className="comparison">
                            <span style={{ width: `${Number(v) * 100}%` }} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="fine-print">
                    {data.quality.primaryQueries.toLocaleString()} queries (
                    {(data.quality.primaryQueryFraction * 100).toFixed(2)}% of
                    validation) had at least one known-catalog target. Primary
                    metrics use this population; ideal NDCG includes unretrieved
                    positives.
                  </p>
                </div>
                <div>
                  <h3>What this demonstrates</h3>
                  <p>
                    Recommendations exported using the approved bundle’s loader,
                    scores and tie-breaking. The public page contains only five
                    anonymous examples. The IAM-protected Cloud Run backend
                    remains private.
                  </p>
                  <h3>What it doesn’t prove</h3>
                  <p>
                    Gains are mainly repeat purchases; discovery is weak. No
                    business lift has been measured. Final held-out evaluation
                    is deferred. Cold-start latency and alert email delivery
                    remain unverified.
                  </p>
                  <p className="fine-print">
                    Serving data cutoff: May 1, 2011. Model training and serving
                    data windows are distinct. No future labels are used for
                    profile selection or display.
                  </p>
                  <a
                    href={
                      data.metadata.repository +
                      "/blob/main/docs/public-demo.md"
                    }
                  >
                    Read the methodology{" "}
                  </a>
                </div>
              </div>
              <div className="version">
                <span>MODEL {data.metadata.modelVersion}</span>
                <span>
                  ARTIFACT SOURCE{" "}
                  {data.metadata.artifactSourceCommit.slice(0, 7)}
                </span>
              </div>
            </div>
          </details>
        </section>
      </main>
      <footer>
        <a className="wordmark" href="#">
          re<span>/</span>order.
        </a>
        <p>Built to show the decisions behind a recommendation.</p>
        <a href={data.metadata.repository}>Explore the project </a>
      </footer>
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
