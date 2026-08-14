import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  outputDir: './.playwright-results',
  use: { baseURL: 'http://localhost:8080' },
  workers: 1,
})
