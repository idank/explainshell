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

test("man page not found", async ({ page }) => {
  await page.goto("/explain?cmd=nonexistentcommand+--help");
  await page.waitForLoadState("networkidle");

  await expect(page.locator("text=missing man page")).toBeVisible();
  await expect(page).toHaveScreenshot("missing-manpage.png", { fullPage: true });
});

test("hover highlights help box", async ({ page }) => {
  await page.goto("/explain?cmd=tar+xzvf+archive.tar.gz");
  await page.waitForLoadState("networkidle");

  const helpBoxes = page.locator("#help .help-box");
  await expect(helpBoxes.first()).toBeVisible();

  // Hover over the first help box to trigger highlight effect
  await helpBoxes.first().hover();

  // Wait for the hover effect to apply
  await page.waitForTimeout(300);

  await expect(page).toHaveScreenshot("explain-hover.png");
});

test("unicode characters in echo command", async ({ page }) => {
  await page.goto("/explain?cmd=echo+%22R%C3%A9sum%C3%A9+caf%C3%A9+cr%C3%A8me+br%C3%BBl%C3%A9e%22");
  await page.waitForLoadState("networkidle");

  // Verify the page rendered without errors
  await expect(page.locator("#command")).toBeVisible();
  await expect(page.locator("text=missing man page")).not.toBeVisible();
  await expect(page.locator("text=error")).not.toBeVisible();

  // Verify unicode characters appear in the rendered command
  const commandText = await page.locator("#command").textContent();
  expect(commandText).toContain("Résumé");
  expect(commandText).toContain("café");
  expect(commandText).toContain("crème");
  expect(commandText).toContain("brûlée");

  // Verify help boxes rendered
  const helpBoxes = page.locator("#help .help-box");
  await expect(helpBoxes.first()).toBeVisible();

  await expect(page).toHaveScreenshot("explain-unicode.png", { fullPage: true });
});

test("long explanation scrolls with many help boxes", async ({ page }) => {
  await page.goto(
    "/explain?cmd=gcc+-Wall+-Wextra+-O2+-g+-std%3Dc11+-I%2Fusr%2Finclude+-L%2Fusr%2Flib+-lm+-lpthread+-o+program+main.c"
  );
  await page.waitForLoadState("networkidle");

  const helpBoxes = page.locator("#help .help-box");
  const count = await helpBoxes.count();
  expect(count).toBeGreaterThan(10);

  // Scroll down partway so some help boxes are cut off
  await page.evaluate(() => window.scrollBy(0, 600));
  await page.waitForTimeout(300);

  await expect(page).toHaveScreenshot("explain-long-scrolled.png");
});
