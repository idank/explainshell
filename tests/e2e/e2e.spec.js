// @ts-check
const fs = require('node:fs');
const path = require('node:path');
const { test, expect } = require('@playwright/test');

// Dedicated database for e2e tests, built by `make e2e-db`.
// The server is started with DB_PATH pointing here (see playwright.config.js).
const E2E_DB = path.join(__dirname, 'e2e.db');

test('e2e database exists', () => {
    expect(fs.existsSync(E2E_DB)).toBe(true);
});

test('homepage loads', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/explainshell/i);
    await expect(page.locator('input#explain')).toBeVisible();
    await expect(page).toHaveScreenshot('homepage.png');
});

test('explain sample command', async ({ page }) => {
    await page.goto('/explain?cmd=tar+xzvf+archive.tar.gz&deterministic');
    await page.waitForLoadState('networkidle');

    await expect(page).toHaveScreenshot('explain-sample.png', {
        fullPage: true,
    });

    // Structural assertions
    await expect(page).toHaveTitle(/tar xzvf archive\.tar\.gz/);
    await expect(page.locator('#command')).toBeVisible();

    const helpBoxes = page.locator('#help .help-box');
    await expect(helpBoxes.first()).toBeVisible();

    // Verify help boxes have solid borders
    const count = await helpBoxes.count();
    for (let i = 0; i < count; i++) {
        const border = await helpBoxes
            .nth(i)
            .evaluate((el) => getComputedStyle(el).borderStyle);
        expect(border).toBe('solid');
    }

    // Verify SVG connecting lines exist
    const svgPaths = page.locator('#canvas path');
    await expect(svgPaths.first()).toBeAttached();
});

test('man page not found', async ({ page }) => {
    await page.goto('/explain?cmd=nonexistentcommand+--help');
    await page.waitForLoadState('networkidle');

    await expect(page.locator('text=missing man page')).toBeVisible();
    await expect(page).toHaveScreenshot('missing-manpage.png', {
        fullPage: true,
    });
});

test('hover highlights help box', async ({ page }) => {
    await page.goto('/explain?cmd=tar+xzvf+archive.tar.gz&deterministic');
    await page.waitForLoadState('networkidle');

    const helpBoxes = page.locator('#help .help-box');
    await expect(helpBoxes.first()).toBeVisible();

    // Hover over the first help box to trigger highlight effect
    await helpBoxes.first().hover();

    // Wait for the hover effect to apply
    await page.waitForTimeout(300);

    await expect(page).toHaveScreenshot('explain-hover.png');
});

test('unicode characters in echo command', async ({ page }) => {
    await page.goto(
        '/explain?cmd=echo+%22R%C3%A9sum%C3%A9+caf%C3%A9+cr%C3%A8me+br%C3%BBl%C3%A9e%22&deterministic',
    );
    await page.waitForLoadState('networkidle');

    // Verify the page rendered without errors
    await expect(page.locator('#command')).toBeVisible();
    await expect(page.locator('text=missing man page')).not.toBeVisible();
    await expect(page.locator('text=error')).not.toBeVisible();

    // Verify unicode characters appear in the rendered command
    const commandText = await page.locator('#command').textContent();
    expect(commandText).toContain('Résumé');
    expect(commandText).toContain('café');
    expect(commandText).toContain('crème');
    expect(commandText).toContain('brûlée');

    // Verify help boxes rendered
    const helpBoxes = page.locator('#help .help-box');
    await expect(helpBoxes.first()).toBeVisible();

    await expect(page).toHaveScreenshot('explain-unicode.png', {
        fullPage: true,
    });
});

test('manpage source links use configured URL', async ({ page }) => {
    await page.goto('/explain?cmd=grep+-i+hello');
    await page.waitForLoadState('networkidle');

    // Footer should contain "source manpages:" with a link to Ubuntu manpages
    const footerLink = page.locator('a[href*="manpages.ubuntu.com"]').first();
    await expect(footerLink).toBeVisible();

    const href = await footerLink.getAttribute('href');
    // Should point to manpages.ubuntu.com, not the old hardcoded precise release
    expect(href).toMatch(/manpages\.ubuntu\.com\/manpages\/\w+\//);
    expect(href).not.toContain('/manpages/precise/');
    expect(href).toContain('grep.1.html');
});

test('distro-prefixed URL loads and preserves prefix in links', async ({
    page,
}) => {
    await page.goto('/explain/ubuntu/26.04?cmd=tar+xzvf+archive.tar.gz');
    await page.waitForLoadState('networkidle');

    await expect(page).toHaveTitle(/tar xzvf archive\.tar\.gz/);
    await expect(page.locator('#command')).toBeVisible();

    // Form action should include distro prefix
    const formAction = await page
        .locator('#top-search')
        .evaluate((el) => el.closest('form').getAttribute('action'));
    expect(formAction).toBe('/explain/ubuntu/26.04');

    // Command links within the page should preserve the distro prefix
    const commandLinks = page.locator(
        '#command a[href*="/explain/ubuntu/26.04/"]',
    );
    const count = await commandLinks.count();
    expect(count).toBeGreaterThan(0);
});

test('distro switch navigates to correct URL', async ({ page }) => {
    // Start on arch/latest with tar
    await page.goto('/explain/arch/latest?cmd=tar+xzvf+archive.tar.gz');
    await page.waitForLoadState('networkidle');

    // Open the command's dropdown
    const caret = page.locator('#command .dropdown .caret').first();
    await caret.click();

    // Click the ubuntu 26.04 distro link
    const ubuntuLink = page.locator(
        '#command a[href*="/explain/ubuntu/26.04"]',
    );
    await expect(ubuntuLink).toBeVisible();

    // Click and wait for navigation
    await Promise.all([page.waitForNavigation(), ubuntuLink.click()]);

    // Should navigate to /explain/ubuntu/26.04?cmd=... (not /explain/ubuntu/26.04/arch/latest?cmd=...)
    const url = page.url();
    expect(url).toContain('/explain/ubuntu/26.04');
    expect(url).not.toContain('/arch/latest');
    expect(url).toContain('cmd=');
});

test('distro dropdown shows active distro as unclickable highlighted item', async ({
    page,
}) => {
    await page.goto('/explain?cmd=tar+xzvf+archive.tar.gz');
    await page.waitForLoadState('networkidle');

    // Open the command's dropdown (contains other manpages + distro options)
    const caret = page.locator('#command .dropdown .caret').first();
    await caret.click();

    // The active distro should be a plain <li> (no <a> tag) with the active-distro class
    const activeItem = page.locator('#command .active-distro').first();
    await expect(activeItem).toBeVisible();
    await expect(activeItem).toHaveText(/ubuntu 26\.04/);

    // It should not contain a link
    const link = activeItem.locator('a');
    await expect(link).toHaveCount(0);

    // It should have a distinct background color
    const bg = await activeItem.evaluate(
        (el) => getComputedStyle(el).backgroundColor,
    );
    expect(bg).not.toBe('rgba(0, 0, 0, 0)');
});

test('long explanation scrolls with many help boxes', async ({ page }) => {
    await page.goto(
        '/explain?cmd=tar+--create+--gzip+--verbose+--file+archive.tar.gz+--exclude+*.log+--anchored+--no-recursion+--keep-old-files+--one-file-system+--totals+--checkpoint+src/&deterministic',
    );
    await page.waitForLoadState('networkidle');

    const helpBoxes = page.locator('#help .help-box');
    const count = await helpBoxes.count();
    expect(count).toBeGreaterThan(10);

    // Scroll down partway so some help boxes are cut off
    await page.evaluate(() => window.scrollBy(0, 600));
    await page.waitForTimeout(300);

    // The command wrapper should remain visible (sticky/affix behavior)
    await expect(page.locator('#command-wrapper')).toBeInViewport();

    // SVG canvas should still have lines drawn for visible help boxes
    await expect(page.locator('#canvas path').first()).toBeAttached();

    await expect(page).toHaveScreenshot('explain-long-scrolled.png');
});

test('multi-command navigation with prev/next buttons', async ({ page }) => {
    // Piped command produces shell + command0 + command1 groups, enabling navigation
    await page.goto('/explain?cmd=echo+hello+%7C+grep+hello&deterministic');
    await page.waitForLoadState('networkidle');

    // Navigation UI should appear with prev/next buttons
    const prevNext = page.locator('#prevnext');
    await expect(prevNext).toBeVisible();

    // Should start on "all" view with both commands' help visible
    const currentLabel = prevNext.locator('u');
    await expect(currentLabel).toHaveText('all');

    const helpBoxes = page.locator('#help .help-box');
    const initialCount = await helpBoxes
        .filter({ has: page.locator(':visible') })
        .count();
    expect(initialCount).toBeGreaterThan(0);

    // Click next to navigate to first group
    const nextBtn = prevNext.locator('li .icon-arrow-right').locator('..');
    await nextBtn.click();
    await page.waitForTimeout(200);

    // Current label should change away from "all"
    await expect(currentLabel).not.toHaveText('all');

    // SVG lines should still be drawn
    await expect(page.locator('#canvas path').first()).toBeAttached();

    await expect(page).toHaveScreenshot('explain-piped-navigated.png', {
        fullPage: true,
    });
});

test('keyboard navigation between command groups', async ({ page }) => {
    await page.goto('/explain?cmd=echo+hello+%7C+grep+hello&deterministic');
    await page.waitForLoadState('networkidle');

    const currentLabel = page.locator('#prevnext u');
    await expect(currentLabel).toHaveText('all');

    // Press right arrow to navigate to next group
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(200);
    const afterRight = await currentLabel.textContent();
    expect(afterRight).not.toBe('all');

    // Press right again to move forward one more group
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(200);
    const afterSecondRight = await currentLabel.textContent();
    expect(afterSecondRight).not.toBe(afterRight);

    // Press left arrow to go back to the previous group
    await page.keyboard.press('ArrowLeft');
    await page.waitForTimeout(200);
    await expect(currentLabel).toHaveText(afterRight);

    // Keyboard nav should be disabled when search box is focused
    await page.locator('#top-search').focus();
    const beforeKey = await currentLabel.textContent();
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(200);
    await expect(currentLabel).toHaveText(beforeKey);

    await expect(page).toHaveScreenshot('explain-keyboard-nav.png', {
        fullPage: true,
    });
});

test('theme switching via settings dropdown', async ({ page }) => {
    await page.goto('/explain?cmd=tar+xzvf+archive.tar.gz&deterministic');
    await page.waitForLoadState('networkidle');

    // Should start with default theme
    const body = page.locator('body');
    await expect(body).toHaveAttribute('data-theme', 'default');

    // Open settings dropdown and click Dark
    await page.locator('#settingsContainer > a').click();
    await page.locator('a[data-theme-name="dark"]').click();

    // Body should switch to dark theme
    await expect(body).toHaveAttribute('data-theme', 'dark');

    // Theme should persist across reload (stored in cookie)
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(page.locator('body')).toHaveAttribute('data-theme', 'dark');

    await expect(page).toHaveScreenshot('explain-dark-theme.png', {
        fullPage: true,
    });
});

test('switching from dark back to light removes all dark styles', async ({
    page,
}) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Switch to dark
    await page.locator('#settingsContainer > a').click();
    await page.locator('a[data-theme-name="dark"]').click();
    await expect(page.locator('body')).toHaveAttribute('data-theme', 'dark');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

    // Switch back to light
    await page.locator('#settingsContainer > a').click();
    await page.locator('a[data-theme-name="default"]').click();
    await expect(page.locator('body')).toHaveAttribute('data-theme', 'default');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'default');

    await expect(page).toHaveScreenshot('home-dark-to-light.png', {
        fullPage: true,
    });
});

test('unknown args show question mark circles', async ({ page }) => {
    // grep -Q is not a valid flag, should show as unknown with a ? circle
    await page.goto('/explain?cmd=grep+-Q+hello&deterministic');
    await page.waitForLoadState('networkidle');

    // The unknown span should exist in #command
    const unknownSpan = page.locator('#command span.unknown');
    await expect(unknownSpan).toBeVisible();
    await expect(unknownSpan).toHaveText('-Q hello');
    await expect(unknownSpan).toHaveAttribute(
        'title',
        /no matching help text/i,
    );

    // SVG should contain a circle (the ? marker) for the unknown arg
    const circles = page.locator('#canvas circle');
    await expect(circles.first()).toBeAttached();

    // The ? text should be rendered in the SVG
    const questionMark = page.locator('#canvas text');
    await expect(questionMark.first()).toHaveText('?');

    await expect(page).toHaveScreenshot('explain-unknown-arg.png', {
        fullPage: true,
    });
});

test('expansion popover on hover', async ({ page }) => {
    // echo ~ triggers a tilde expansion span
    await page.goto('/explain?cmd=echo+~&deterministic');
    await page.waitForLoadState('networkidle');

    // The expansion span should exist
    const expansion = page.locator('#command span.expansion-tilde');
    await expect(expansion).toBeVisible();

    // Hover to trigger the Bootstrap popover
    await expansion.hover();
    await page.waitForTimeout(300);

    // Popover should appear with the expansion title
    const popover = page.locator('.popover');
    await expect(popover).toBeVisible();
    await expect(popover).toContainText('Tilde Expansion');

    await expect(page).toHaveScreenshot('explain-expansion-popover.png', {
        fullPage: true,
    });
});

test('search box is populated from URL query', async ({ page }) => {
    await page.goto('/explain?cmd=echo+hello');
    await page.waitForLoadState('networkidle');

    const searchBox = page.locator('#top-search');
    await expect(searchBox).toHaveValue('echo hello');
});

test('right-going SVG links do not overlap', async ({ page }) => {
    await page.goto('/explain?cmd=echo+hello+%7C+grep+foo&deterministic');
    await page.waitForLoadState('networkidle');

    await expect(page.locator('#command')).toBeVisible();
    await expect(page).toHaveScreenshot('explain-no-overlap-links.png', {
        fullPage: true,
    });
});

test('blockquotes in help boxes render as plain indented text', async ({
    page,
}) => {
    await page.goto('/explain/1/git-rebase');
    await page.waitForLoadState('networkidle');

    // The --empty option contains blockquotes in its description
    const helpBox = page.locator('.help-box', {
        hasText: '--empty=(drop|keep|stop)',
    });
    await expect(helpBox).toBeVisible();
    await expect(helpBox.locator('blockquote').first()).toBeVisible();

    await expect(helpBox).toHaveScreenshot('help-box-blockquote.png');
});

test('distro picker defaults to all with /explain action', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const picker = page.locator('#distroPicker');
    const button = page.locator('#distroPickerButton');
    await expect(button).toHaveText(/all/);
    await expect(page.locator('#distroPickerMenu')).not.toHaveClass(/open/);
    await expect(page.locator('#explain-form')).toHaveAttribute(
        'action',
        '/explain',
    );

    await expect(picker).toHaveScreenshot('distro-picker-closed.png');
});

test('distro picker opens and lists all available distros', async ({
    page,
}) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await page.locator('#distroPickerButton').click();

    const menu = page.locator('#distroPickerMenu');
    await expect(menu).toHaveClass(/open/);

    const items = menu.locator('li a');
    await expect(items).toHaveText([
        'all',
        'arch/latest',
        'ubuntu/24.04',
        'ubuntu/26.04',
    ]);

    // Clip covers the picker button plus the menu that extends below it.
    const btnBox = await page.locator('#distroPickerButton').boundingBox();
    if (!btnBox) throw new Error('button not rendered');
    await expect(page).toHaveScreenshot('distro-picker-open.png', {
        clip: {
            x: Math.max(0, btnBox.x - 4),
            y: Math.max(0, btnBox.y - 4),
            width: 260,
            height: btnBox.height + 160,
        },
    });
});

test('selecting a distro updates label and form action', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await page.locator('#distroPickerButton').click();
    await page
        .locator(
            '#distroPickerMenu a[data-distro="ubuntu"][data-release="26.04"]',
        )
        .click();

    // Menu closes, label updates, action pins to ubuntu/26.04.
    await expect(page.locator('#distroPickerMenu')).not.toHaveClass(/open/);
    await expect(page.locator('.distro-picker-label')).toHaveText(
        'ubuntu/26.04',
    );
    await expect(page.locator('#explain-form')).toHaveAttribute(
        'action',
        '/explain/ubuntu/26.04',
    );

    await expect(page.locator('#distroPicker')).toHaveScreenshot(
        'distro-picker-selected.png',
    );

    // Switching back to 'all' reverts the action.
    await page.locator('#distroPickerButton').click();
    await page
        .locator('#distroPickerMenu a[data-distro=""][data-release=""]')
        .click();
    await expect(page.locator('.distro-picker-label')).toHaveText('all');
    await expect(page.locator('#explain-form')).toHaveAttribute(
        'action',
        '/explain',
    );
});

test('submitting a pinned distro navigates to the scoped URL', async ({
    page,
}) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await page.locator('#distroPickerButton').click();
    await page
        .locator(
            '#distroPickerMenu a[data-distro="arch"][data-release="latest"]',
        )
        .click();

    await page.locator('#explain').fill('tar xzvf archive.tar.gz');
    await Promise.all([
        page.waitForURL(/\/explain\/arch\/latest\?cmd=/),
        page.locator('#explain-form button[type="submit"]').click(),
    ]);

    expect(page.url()).toContain('/explain/arch/latest?cmd=');
});

test('help tooltip reveals distro picker explanation on hover', async ({
    page,
}) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const tooltip = page.locator('.distro-picker-tooltip');
    await expect(tooltip).toBeHidden();

    await page.locator('.distro-picker-help').hover();
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toContainText(/distro.*manpages/i);

    // Crop covers the button + the tooltip above it.
    const button = page.locator('#distroPickerButton');
    const btnBox = await button.boundingBox();
    if (!btnBox) throw new Error('button not rendered');
    await expect(page).toHaveScreenshot('distro-picker-tooltip.png', {
        clip: {
            x: Math.max(0, btnBox.x - 120),
            y: Math.max(0, btnBox.y - 80),
            width: btnBox.width + 240,
            height: btnBox.height + 90,
        },
    });
});
