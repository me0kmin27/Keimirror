const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests',
  use: { baseURL: 'http://127.0.0.1:18763' },
  webServer: {
    command: 'python3 tests/serve.py',
    url: 'http://127.0.0.1:18763',
    reuseExistingServer: false,
    stdout: 'ignore',
    stderr: 'ignore',
  },
});
