/**
 * Page tests: the built UI (`npm run build`, as the container runs it)
 * against the fake API in tests/fake-api.ts, in headless Chromium.
 *
 *   npm run build && npm run test:pages
 */
import { defineConfig, devices } from '@playwright/test';
import { randomBytes, scryptSync } from 'node:crypto';
import { API_KEY, PASSWORD, SIGNED_IN } from './tests/fixtures.ts';

/** The page tests' password, hashed as `npm run password` would (with a
 * smaller N, which the hash records, so the tests stay quick). */
function testHash(password: string): string {
	const salt = randomBytes(16);
	const key = scryptSync(password, salt, 32, { N: 1024, r: 8, p: 1 });
	return ['scrypt', 1024, 8, 1, salt.toString('base64url'), key.toString('base64url')].join(':');
}

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
	projects: [
		// Signs in once and keeps the cookie for every page test.
		{ name: 'sign-in', testMatch: 'sign-in.setup.ts' },
		{
			name: 'pages',
			dependencies: ['sign-in'],
			testIgnore: 'login.spec.ts',
			use: { storageState: SIGNED_IN }
		},
		// Signed out: the sign-in page itself.
		{ name: 'login', testMatch: 'login.spec.ts' }
	],
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
				BLT_API_KEY: API_KEY,
				BLT_UI_PASSWORD_HASH: testHash(PASSWORD),
				BLT_UI_SESSION_SECRET: randomBytes(32).toString('base64url')
			},
			reuseExistingServer: false
		}
	]
});
