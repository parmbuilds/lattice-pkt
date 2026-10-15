import { defineConfig } from '@playwright/test'

// Runs against a live stack (docker compose up + seed). Tests share one database, so run serially.
export default defineConfig({
  testDir: 'e2e',
  workers: 1,
  timeout: 60_000,
  use: {
    baseURL: process.env.LATTICE_E2E_URL ?? 'http://localhost:8000',
    trace: 'retain-on-failure',
  },
})
