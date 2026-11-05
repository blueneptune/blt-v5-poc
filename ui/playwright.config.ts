/**
 * Page tests: the built UI (`npm run build`, as the container runs it)
 * against the fake API in tests/fake-api.ts, in headless Chromium.
 *
 *   npm run build && npm run test:pages
 */
import { defineConfig, devices } from '@playwright/test';
import { API_KEY } from './tests/fixtures.ts';

const API_PORT = 4199;
const UI_PORT = 4173;

export default defineConfig({
	testDir: 'tests',
	testMatch: '*.spec.ts',
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
	use: {
		baseURL: `http://127.0.0.1:${UI_PORT}`,
		...devices['Desktop Chrome'],
		viewport: { width: 1400, height: 800 },
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure'
	},
	webServer: [
		{
			command: 'node tests/fake-api.ts',
			url: `http://127.0.0.1:${API_PORT}/v1/sources`,
			env: { FAKE_API_PORT: String(API_PORT) },
			// Up once it answers, even 401 for want of the key.
			reuseExistingServer: false
		},
		{
			command: 'node build',
			url: `http://127.0.0.1:${UI_PORT}/healthz`,
			env: {
				HOST: '127.0.0.1',
				PORT: String(UI_PORT),
				ORIGIN: `http://127.0.0.1:${UI_PORT}`,
				BLT_API_URL: `http://127.0.0.1:${API_PORT}`,
				BLT_API_KEY: API_KEY
			},
			reuseExistingServer: false
		}
	]
});
