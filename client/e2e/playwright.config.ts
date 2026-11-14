import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  outputDir: '../.output/e2e',
  reporter: 'list',
  timeout: 60_000,
});
