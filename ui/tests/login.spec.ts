/** Signed out: every page sends you to sign in, and back afterwards. */
import { expect, test } from '@playwright/test';
import { PASSWORD, USER } from './fixtures.ts';

test('a page needs signing in, then opens', async ({ page }) => {
	await page.goto('/storage?group=kind');
	await expect(page).toHaveURL(/\/login\?next=%2Fstorage%3Fgroup%3Dkind$/);
	await expect(page.getByRole('navigation')).toHaveCount(0);

	await page.getByLabel('Name').fill(USER);
	await page.getByLabel('Password').fill('not the password');
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('alert')).toHaveText('That name and password do not match.');
	await expect(page.getByLabel('Name')).toHaveValue(USER);

	await page.getByLabel('Password').fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page).toHaveURL(/\/storage\?group=kind$/);
	await expect(page.getByRole('heading', { name: 'Storage' })).toBeVisible();

	await page.getByRole('button', { name: 'Sign out' }).first().click();
	await expect(page).toHaveURL(/\/login$/);
	await page.goto('/jobs');
	await expect(page).toHaveURL(/\/login\?next=%2Fjobs$/);
});

test('the session cookie cannot be read by the page', async ({ page }) => {
	await page.goto('/login');
	await page.getByLabel('Name').fill(USER);
	await page.getByLabel('Password').fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();
	const cookies = await page.context().cookies();
	const session = cookies.find((c) => c.name === 'blt_session');
	expect(session).toMatchObject({ httpOnly: true, sameSite: 'Lax', path: '/' });
	expect(await page.evaluate(() => document.cookie)).not.toContain('blt_session');
});

test('the probe needs no sign-in, and next never leaves the site', async ({ page, request }) => {
	expect((await request.get('/healthz')).status()).toBe(200);
	await page.goto('/login?next=//evil.example/');
	await page.getByLabel('Name').fill(USER);
	await page.getByLabel('Password').fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page).toHaveURL(/127\.0\.0\.1:\d+\/$/);
});
