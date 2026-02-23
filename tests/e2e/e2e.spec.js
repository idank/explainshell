// @ts-check
const { test, expect } = require("@playwright/test");

test("homepage loads", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/explainshell/i);
  await expect(page.locator("input#explain")).toBeVisible();
  await expect(page).toHaveScreenshot("homepage.png");
});

test("explain sample command", async ({ page }) => {
  await page.goto("/explain?cmd=tar+xzvf+archive.tar.gz");
  await page.waitForLoadState("networkidle");

  await expect(page).toHaveScreenshot("explain-sample.png", { fullPage: true });

  // Structural assertions
  await expect(page).toHaveTitle(/tar xzvf archive\.tar\.gz/);
  await expect(page.locator("#command")).toBeVisible();

  const helpBoxes = page.locator("#help .help-box");
  await expect(helpBoxes.first()).toBeVisible();

  // Verify help boxes have solid borders
  const count = await helpBoxes.count();
  for (let i = 0; i < count; i++) {
    const border = await helpBoxes.nth(i).evaluate(
      (el) => getComputedStyle(el).borderStyle
    );
    expect(border).toBe("solid");
  }

  // Verify SVG connecting lines exist
  const svgPaths = page.locator("#canvas path");
  await expect(svgPaths.first()).toBeAttached();
});
