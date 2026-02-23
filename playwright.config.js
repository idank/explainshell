// @ts-check
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "tests/e2e",
  testMatch: "*.spec.js",
  snapshotPathTemplate: "{testDir}/snapshots/{arg}{ext}",
  reporter: [["html", { open: "never" }]],
  outputDir: "test-results",
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
  webServer: {
    command: "python runserver.py",
    url: "http://127.0.0.1:5000",
    reuseExistingServer: true,
  },
  use: {
    baseURL: "http://127.0.0.1:5000",
  },
  expect: {
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.01,
    },
  },
});
