import { expect, test } from "@playwright/test";

test("sample suggestions can be selected and copied", async ({ page }) => {
  const copied: string[] = [];
  await page.exposeFunction("captureList", (value: string) =>
    copied.push(value),
  );
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: (value: string) =>
          (
            window as unknown as {
              captureList: (list: string) => Promise<void>;
            }
          ).captureList(value),
      },
    });
  });
  await page.goto("");
  await page.getByRole("link", { name: "Grocery", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Before your grocery trip" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Suggest items" }).click();
  await expect(page.getByRole("checkbox")).toHaveCount(4);
  await expect(page.getByText(/usually buy eggs every 8 days/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Copy shopping list" }),
  ).toBeDisabled();
  await page.getByRole("checkbox", { name: /^eggs/ }).check();
  await page.getByRole("checkbox", { name: /^milk/ }).check();
  await page.getByRole("button", { name: "Copy shopping list" }).click();
  await expect(page.getByRole("status")).toHaveText("Shopping list copied.");
  expect(copied).toEqual(["eggs\nmilk"]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("personal trips stay local, survive reload and can be corrected", async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: {
        writeText: () => Promise.reject(new Error("Clipboard not granted")),
      },
    });
  });
  await page.goto("?mode=grocery");
  const requests: string[] = [];
  const favicon = new URL("favicon.svg", page.url()).href;
  page.on("request", (request) => {
    if (request.url() !== favicon || request.method() !== "GET") {
      requests.push(request.url());
    }
  });
  await page.getByRole("button", { name: "My trips" }).click();
  await expect(
    page.getByRole("button", { name: "Suggest items" }),
  ).toBeDisabled();
  await page.getByLabel("Purchase date").fill("2026-09-01");
  await page.getByLabel("Items bought").fill("Rice\n rice \nGreek yogurt");
  await page.getByRole("button", { name: "Add past trip" }).click();
  await page.getByLabel("Planned shopping date").fill("2026-09-09");
  await page.getByRole("button", { name: "Suggest items" }).click();
  await expect(page.getByRole("checkbox")).toHaveCount(2);
  await expect(page.getByText(/You bought Rice on 1 trip/)).toBeVisible();
  await page.getByRole("checkbox", { name: /^Rice/ }).check();
  await page.getByRole("button", { name: "Copy shopping list" }).click();
  await expect(page.getByLabel("Select and copy your list")).toHaveValue(
    "Rice",
  );
  expect(requests).toEqual([]);

  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Your past trips" }),
  ).toBeVisible();
  await page.getByText("View 1 trip", { exact: true }).click();
  await page.getByRole("button", { name: /Edit trip/ }).click();
  await page.getByLabel("Items bought").fill("Tofu");
  await page.getByRole("button", { name: "Save trip" }).click();
  await page.getByLabel("Planned shopping date").fill("2026-09-01");
  await page.getByRole("button", { name: "Suggest items" }).click();
  await expect(page.getByRole("status")).toHaveText(
    "No items to suggest for this date.",
  );
  await page.getByLabel("Planned shopping date").fill("2026-09-09");
  await page.getByRole("button", { name: "Suggest items" }).click();
  await expect(page.getByRole("checkbox")).toHaveCount(1);
  await expect(page.getByRole("checkbox", { name: /^Tofu/ })).toBeVisible();
  await page.getByRole("button", { name: /Remove trip/ }).click();
  await expect(
    page.getByRole("button", { name: "Suggest items" }),
  ).toBeDisabled();
});
