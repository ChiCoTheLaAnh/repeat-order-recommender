import { test, expect } from "@playwright/test";
import demo from "../public/data/demo.json" with { type: "json" };
test("profile switching, top K, badges, chart and fallback", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("");
  await expect(
    page.getByRole("heading", { name: "What comes next?" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Typical history/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("article.product")).toHaveCount(10);
  await expect(
    page.getByRole("img", { name: "Monthly distinct purchase invoices" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Top 5", exact: true }).click();
  await expect(page.locator("article.product")).toHaveCount(5);
  const first = await page
    .locator("article.product .sku")
    .first()
    .textContent();
  await page.getByRole("button", { name: "Top 20", exact: true }).click();
  await expect(page.locator("article.product")).toHaveCount(20);
  expect(await page.locator("article.product .sku").first().textContent()).toBe(
    first,
  );
  await page.getByRole("button", { name: /Lighter history/ }).click();
  await expect(
    page.getByRole("button", { name: /Lighter history/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByText("View activity data", { exact: true }).click();
  await expect(page.getByRole("cell", { name: "Nov 2010" })).toBeVisible();
  await page.getByRole("button", { name: /Unknown customer/ }).click();
  await expect(
    page.getByRole("heading", { name: "No personal history" }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: "Monthly distinct purchase invoices" }),
  ).toHaveCount(0);
  await expect(page.locator(".badge")).toHaveText(
    Array(20).fill("Popular pick"),
  );
  for (const profile of demo.profiles.filter(
    (p) => p.mode === "personalized",
  )) {
    await page.getByRole("button", { name: new RegExp(profile.label) }).click();
    await expect(page.locator("article.product .sku").first()).toHaveText(
      `SKU ${profile.recommendations[0].sku}`,
    );
    const expected = profile.recommendations.filter(
      (i) => i.previouslyPurchased,
    ).length;
    await expect(page.locator(".badge.repeat")).toHaveCount(expected);
  }
  await page.reload();
  await expect(
    page.getByRole("button", { name: /Typical history/ }),
  ).toHaveAttribute("aria-pressed", "true");
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
  expect(errors).toEqual([]);
});
test("data failure is explicit and retry works", async ({ page }) => {
  let fail = true;
  await page.route("**/data/demo.json", (route) =>
    fail
      ? route.fulfill({ status: 503, body: "unavailable" })
      : route.continue(),
  );
  await page.goto("");
  await expect(
    page.getByRole("heading", { name: "The demo couldn’t load." }),
  ).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("heading", { name: "What comes next?" }),
  ).toBeVisible();
});
test("keyboard, product history, methodology and repository assets", async ({
  page,
}) => {
  await page.goto("");
  await page.getByRole("button", { name: "Top 5", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("article")).toHaveCount(5);
  await page.locator(".product summary").first().click();
  await expect(page.locator(".signals").first()).toContainText(
    "not a purchase probability",
  );
  await page.getByText("A simple interface. A careful pipeline.").click();
  await expect(
    page.getByRole("heading", { name: "Why this model?" }),
  ).toBeVisible();
  expect(await page.locator("script[src]").getAttribute("src")).toMatch(
    /^\/repeat-order-recommender\/assets\//,
  );
  await page.screenshot({
    path: `../outputs/web-demo/${test.info().project.name}.png`,
    fullPage: true,
  });
});

test("shopping comparison is real, profile-specific and absent for a guest", async ({
  page,
}) => {
  await page.goto("");
  await expect(
    page.getByRole("heading", {
      name: "Your customer is back. What should you show them?",
    }),
  ).toBeVisible();
  const comparison = page.getByRole("region", {
    name: "Bestseller and personalized comparison",
  });
  await expect(comparison).toBeVisible();
  const popular = page.getByRole("region", {
    name: "Bestsellers for everyone",
  });
  const personal = page.getByRole("region", {
    name: "Personalized for selected shopper",
  });
  await expect(popular.locator("li")).toHaveCount(5);
  await expect(personal.locator("li")).toHaveCount(5);
  const originalPopular = await popular.locator("li").allTextContents();
  await expect(comparison).toContainText(
    "0 of 5 products appear in both lists.",
  );
  for (const p of demo.profiles.filter((p) => p.mode === "personalized")) {
    await page.getByRole("button", { name: new RegExp(p.label) }).click();
    expect(await popular.locator("li").allTextContents()).toEqual(
      originalPopular,
    );
    await expect(personal.locator("li").first()).toContainText(
      `SKU ${p.recommendations[0].sku}`,
    );
  }
  await page.getByRole("button", { name: /Unknown customer/ }).click();
  await expect(comparison).toHaveCount(0);
});
