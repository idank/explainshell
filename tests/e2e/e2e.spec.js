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

test("manpage source links use configured URL", async ({ page }) => {
  await page.goto("/explain?cmd=grep+-i+hello");
  await page.waitForLoadState("networkidle");

  // Footer should contain "source manpages:" with a link to Ubuntu manpages
  const footerLink = page.locator('a[href*="manpages.ubuntu.com"]').first();
  await expect(footerLink).toBeVisible();

  const href = await footerLink.getAttribute("href");
  // Should point to manpages.ubuntu.com, not the old hardcoded precise release
  expect(href).toMatch(/manpages\.ubuntu\.com\/manpages\/\w+\//);
  expect(href).not.toContain("/manpages/precise/");
  expect(href).toContain("grep.1.html");
});

test("distro-prefixed URL loads and preserves prefix in links", async ({ page }) => {
  await page.goto("/explain/ubuntu/25.10?cmd=tar+xzvf+archive.tar.gz");
  await page.waitForLoadState("networkidle");

  await expect(page).toHaveTitle(/tar xzvf archive\.tar\.gz/);
  await expect(page.locator("#command")).toBeVisible();

  // Form action should include distro prefix
  const formAction = await page.locator("#top-search").evaluate(
    (el) => el.closest("form").getAttribute("action")
  );
  expect(formAction).toBe("/explain/ubuntu/25.10");

  // Command links within the page should preserve the distro prefix
  const commandLinks = page.locator('#command a[href*="/explain/ubuntu/25.10/"]');
  const count = await commandLinks.count();
  expect(count).toBeGreaterThan(0);
});

test("distro dropdown shows active distro as unclickable highlighted item", async ({ page }) => {
  await page.goto("/explain?cmd=tar+xzvf+archive.tar.gz");
  await page.waitForLoadState("networkidle");

  // Open the command's dropdown (contains other manpages + distro options)
  const caret = page.locator("#command .dropdown .caret").first();
  await caret.click();

  // The active distro should be a plain <li> (no <a> tag) with the active-distro class
  const activeItem = page.locator("#command .active-distro").first();
  await expect(activeItem).toBeVisible();
  await expect(activeItem).toHaveText(/ubuntu 25\.10/);

  // It should not contain a link
  const link = activeItem.locator("a");
  await expect(link).toHaveCount(0);

  // It should have a distinct background color
  const bg = await activeItem.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(bg).not.toBe("rgba(0, 0, 0, 0)");
});

test("long explanation scrolls with many help boxes", async ({ page }) => {
  await page.goto(
    "/explain?cmd=tar+--create+--gzip+--verbose+--file+archive.tar.gz+--exclude+*.log+--anchored+--no-recursion+--keep-old-files+--one-file-system+--totals+--checkpoint+src/"
  );
  await page.waitForLoadState("networkidle");

  const helpBoxes = page.locator("#help .help-box");
  const count = await helpBoxes.count();
  expect(count).toBeGreaterThan(10);

  // Scroll down partway so some help boxes are cut off
  await page.evaluate(() => window.scrollBy(0, 600));
  await page.waitForTimeout(300);

  await expect(page).toHaveScreenshot("explain-long-scrolled.png", {
    maxDiffPixelRatio: 0.03,
  });
});

test("blockquotes in help boxes render as plain indented text", async ({ page }) => {
  await page.goto("/explain/1/git-rebase");
  await page.waitForLoadState("networkidle");

  // The --empty option contains blockquotes in its description
  const helpBox = page.locator(".help-box", {
    hasText: "--empty=(drop|keep|stop)",
  });
  await expect(helpBox).toBeVisible();
  await expect(helpBox.locator("blockquote").first()).toBeVisible();

  await expect(helpBox).toHaveScreenshot("help-box-blockquote.png");
});
