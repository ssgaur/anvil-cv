import { expect, test } from "@playwright/test";

const SAMPLE_IMAGE = "/Users/shailendrasingh/PersonalDev/anvil-cv/tmp/cucumber.jpg";

test("anvil end-to-end: login → project → dataset → upload → embed → auto-annotate", async ({ page }) => {
  test.setTimeout(180_000);

  page.on("console", (m) => {
    if (m.type() === "error") console.log("[browser-error]", m.text());
  });

  // 1. Login
  await page.goto("/login");
  await page.getByPlaceholder("email").fill("admin@anvil.dev");
  await page.getByPlaceholder("password").fill("admin");
  await page.locator('button[type="submit"], button:not([type="button"])').filter({ hasText: /^Sign in$/ }).click();
  await expect(page).toHaveURL(/\/projects$/);

  // 2. Create project (unique name per run)
  const projectName = `E2E ${Date.now()}`;
  await page.getByPlaceholder(/New project name/i).fill(projectName);
  await page.getByRole("button", { name: /Create$/ }).click();
  await expect(page.getByRole("link", { name: projectName })).toBeVisible();

  // 3. Open project, add a class
  await page.getByRole("link", { name: projectName }).click();
  await page.getByRole("button", { name: /^Classes$/ }).click();
  await page.getByPlaceholder(/Class name/i).fill("cucumber");
  await page.getByRole("button", { name: /Add class/i }).click();
  await expect(page.getByText("cucumber").first()).toBeVisible();

  // 4. Create dataset
  await page.getByRole("button", { name: /^Data$/ }).click();
  await page.getByPlaceholder(/New dataset name/i).fill("Batch 01");
  await page.getByRole("button", { name: /Create dataset/i }).click();
  await page.getByRole("link", { name: /Batch 01/ }).click();

  // 5. Upload image
  const fileInput = page.locator('input[type="file"]');
  await fileInput.setInputFiles(SAMPLE_IMAGE);

  // Wait for image card
  const card = page.locator('a[href*="/workview/"]').first();
  await expect(card).toBeVisible({ timeout: 30_000 });

  // Wait for ready (green dot ~0.625rem -> tailwind w-2.5 h-2.5 + bg-emerald-500 + rounded-full)
  await expect(async () => {
    const greenDot = page.locator('div.bg-emerald-500.rounded-full').first();
    await expect(greenDot).toBeVisible({ timeout: 2000 });
  }).toPass({ timeout: 90_000 });

  // 6. Open workview
  await page.locator('a[href*="/workview/"]').first().click();
  await expect(page).toHaveURL(/\/workview\//);

  // Wait for canvas + image fit
  await page.waitForSelector("canvas", { timeout: 15_000 });
  await page.waitForTimeout(3000);

  // 7. Click center — auto-annotate
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("no canvas bbox");
  await canvas.click({ position: { x: box.width / 2, y: box.height / 2 }, force: true });

  // 8. Wait for SAM2 result
  await expect(page.getByText(/SAM2 score/)).toBeVisible({ timeout: 30_000 });

  // 9. Save
  await page.getByRole("button", { name: /Save \(Enter\)/ }).click();

  // 10. Annotation appears
  await expect(page.getByText(/cucumber #1/)).toBeVisible({ timeout: 10_000 });

  await page.screenshot({ path: "e2e-result.png", fullPage: true });
});
