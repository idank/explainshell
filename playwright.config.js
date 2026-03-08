// @ts-check
const net = require("net");
const path = require("path");
const { defineConfig } = require("@playwright/test");

const E2E_DB = path.join(__dirname, "tests", "e2e", "e2e.db");

function getFreePort() {
  const srv = net.createServer();
  srv.listen(0);
  const port = srv.address().port;
  srv.close();
  return port;
}

// Cache the port in an env var so Playwright workers (separate processes that
// re-evaluate this config) use the same port as the main process.
if (!process.env._PLAYWRIGHT_PORT) {
  process.env._PLAYWRIGHT_PORT = String(getFreePort());
}
const port = Number(process.env._PLAYWRIGHT_PORT);

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
    command: ". .venv/bin/activate && python runserver.py",
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    env: { PORT: String(port), DEBUG: "false", DB_PATH: E2E_DB },
  },
  use: {
    baseURL: `http://127.0.0.1:${port}`,
  },
  expect: {
    toHaveScreenshot: {},
  },
});
