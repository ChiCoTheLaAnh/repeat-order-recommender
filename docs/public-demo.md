# Public interactive historical demo

The English single-page site in `web/` is a static product demonstration. It serves a reviewed, precomputed export from the approved logistic release, with **purchases strictly before May 1, 2011**. It does not call Cloud Run, authenticate visitors, run a model in the browser, or accept customer IDs. Cloud Run remains IAM protected.

## Run locally

Requires Node 24. From the repository root:

```sh
npm ci --prefix web
npm test --prefix web
npm run build --prefix web
npm run dev --prefix web
```

Open the printed URL at `/repeat-order-recommender/`. The repository base path is intentional, including for preview and refresh. All assets and JSON are local; no analytics, remote fonts or browser credentials are included.

Browser checks against the production build:

```sh
npx --prefix web playwright install chromium
npm run test:e2e --prefix web
```

On a machine with an existing Chromium installation, set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to its executable. Tests cover desktop and phone layouts, keyboard interaction, all top-K prefixes, profile switching, guest mode, error/retry, activity tables and repository asset paths. Screenshots go to ignored `outputs/web-demo/`.

## Export the approved examples again

Do **not** train a model to produce this website. You need the existing ignored approved bundle and its original cleaned paid-purchases file. If either is absent, transfer the approved artifacts through a private channel and verify the release approval; do not substitute another release.

```sh
uv run --locked python -m scripts.demo_export \
  --bundle outputs/releases/release_v1_35f8ccbb4cd8b9ada5b40696103a25e8f3969f3a54081e310a84354fd81221f6 \
  --paid outputs/cleaning/paid_purchases.parquet \
  --output web/public/data/demo.json
uv run --locked python -m unittest tests.test_demo_export -v
```

The exporter uses `load_approved_bundle`, which verifies the approved manifest/image/source relationship before loading the original release code. The paid file hash must match the approved manifest. Parquet predicate pushdown reads only pre-cutoff rows and the required columns; the full file is hashed for provenance. No target labels or test snapshots are loaded. Original business rules and invoice-based counting are reused. The release scorer supplies ordering, tie-breaking, item descriptions and fallback results; the exporter never rescales or reranks them.

The public JSON is intentionally versioned in Git so Pages needs no private data or model artifacts. Exact field allowlists prevent accidentally serializing full frames or manifests. The generated private receipt at `outputs/demo-export/export_receipt.json` records the public JSON hash, exporter hash, approved manifest hash and release ID. Bundles, data, models, receipts and screenshots remain ignored.

### Profile selection and windows

Profiles come from identified customers with stored serving candidates. Sort by 180-day distinct invoice count, then recency, then internal ID for stable ties. Select the first, lower median and last customer; select the most stale remaining customer fourth (stable ID tie-break). Internal IDs are used only inside the exporter and are never serialized. This is selection on past history, not future performance; the sample is not representative of all customers.

Summary and monthly bars use `[2010-11-02, 2011-05-01)`. The existing query eligibility requires two distinct invoices over all prior history, so a shopper can legitimately have only one invoice inside this narrower display window. November is partial. Invoices are counted distinctly even when they contain many transaction lines; SKUs are distinct as well. Recency is floored to whole days for display, while the export keeps the precise value. The “Previously purchased” badge and item invoice/recency signals use **all available pre-cutoff history**, not only 180 days. New-to-customer is not the same as a future discovery label. Source flags indicate source membership, not causal attribution.

The unknown profile has no personal history or repeat/new badge. It uses the release's distinct-identified-purchaser 30-day fallback. Those counts have different units from logistic margins. Neither is a calibrated probability. Model training windows and serving data cutoff are distinct; existing release maturity checks remain in force.

### Public content and limitations

Only aliases, necessary product IDs/descriptions, aggregates, top-20 results, historical signals and non-sensitive version metadata are exported. No customer/invoice/query IDs, alias mapping, email, complete feature matrix, model or credentials are published. These are pseudonymized historical examples from UCI Online Retail II, not a claim of irreversible anonymization or real-time customer behavior. Descriptions are original retail text, displayed with title styling; no photos, stock or prices are invented.

Validation comparison values are the existing rounded reported results (heuristic NDCG@10 0.3351, logistic 0.378038, XGBoost 0.379499); there is no new evaluation. The primary population is 1,817 queries with a known-catalog target, 23.59% of validation. Gains are mostly repeat purchases; discovery remains weak. Business lift, confirmed cold-start latency and notification email delivery remain unverified. Held-out model evaluation remains deferred. Original 249/250 cloud load results and later 250/250 diagnostics remain separate and unchanged.

## Publish to GitHub Pages

`Offline CI` runs locked npm installation, unit tests, production build and desktop/mobile browser checks, alongside existing Python tests and Docker build. Review and merge the feature PR after both jobs are green.

Repository owner setup (if Pages is not already enabled):

1. Open repository **Settings → Pages**.
2. Under **Build and deployment → Source**, select **GitHub Actions**.
3. Open **Settings → Environments → github-pages** and restrict deployments to `main`.
4. In **Actions → Publish public historical demo → Run workflow**, select reviewed `main`.
5. Confirm the deployment succeeds, then open the URL reported by the job and verify profile switching, JSON/assets and refresh.

The workflow is manual and rejects non-main refs. It uploads only `web/dist`, using temporary GitHub Pages OIDC permissions. It does not build or deploy a Cloud Run image or alter IAM. The expected project URL is `https://ChiCoTheLaAnh.github.io/repeat-order-recommender/`; this is a target address, not proof the site is live. A successful local build alone does not establish public availability.

Updates require re-exporting from the same approved frozen release, checking the diff and private receipt, running tests, reviewing/merging, then manually publishing. A new model, cutoff or schema requires a separate reviewed policy change; it is not an automatic website refresh.

### Deployment permission observed during implementation

The connected GitHub integration could create/review code changes but returned **HTTP 403 `Resource not accessible by integration`** for `POST /repos/ChiCoTheLaAnh/repeat-order-recommender/pages` when enabling a workflow-based Pages site. The owner must perform the Pages Source setup above. No cloud credentials or broader service-account permissions are needed to resolve this GitHub setting. Until a Pages deployment and public URL check succeed, the website should be described as implemented and locally verified, not online.

## Verification recorded for this implementation

- The approved export was generated twice; the public files matched byte-for-byte. SHA256: `4526ab55af9326a495286e7f65648ab93808291229c392057e07d82e2d4cbe3c` (50,097 bytes, five profiles, 100 recommendations).
- Python suite: 116 tests passed, including distinct-invoice boundaries, future-row invariance, deterministic selection, upstream order preservation and strict public field allowlists.
- Frontend: three unit tests passed; production TypeScript/Vite build passed. Six Chromium browser checks passed across desktop (1280×720) and phone (390×664 CSS viewport), including every profile's top result and repeat/new badges against the export. This is Chromium with mobile emulation, not a claim of real-device Safari testing.
- Screenshots were visually inspected at desktop and mobile widths. Smaller labels were darkened after independent review. SVG bars offer hover/focus values and an equivalent data table; repeat/new counts are also readable as text. Motion is not required and reduced-motion preferences are respected.
- No model fit, final-test evaluation, cloud load test or Cloud Run deployment was performed for the website.

## Returning-shopper comparison

The opening scenario is a shop's “Buy again” shelf. A collapsible side-by-side comparison displays the **first five** already-exported popularity fallback products and the selected shopper's first five model results. Popularity means distinct identified purchasers over the preceding 30 days, not revenue or unit sales. The global shelf stays fixed when profiles change; the personalized shelf follows the selected profile. The existing Top 5/10/20 control applies to the detailed product cards below, not this fixed-five comparison.

The shared-product count is the size of the intersection of those two five-SKU sets; it ignores rank matches. It describes different product choices, not recommendation quality, future purchases or business lift. Only the personalized column carries verified past-purchase badges. Baseline products have no invented per-customer history classification. Unknown customers have no personalized comparison.

This presentation reuses the existing public JSON without changing a byte (the SHA256 above remains valid). No export, model, retrieval policy, inference endpoint, target labels or dataset changes are required. Six frontend unit tests and eight desktop/mobile browser checks pass for the revised UI, including prefix preservation, stable global popularity, profile-specific results and unknown-mode exclusion.
