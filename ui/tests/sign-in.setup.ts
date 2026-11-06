/** Signs in once, through the real page, for every page test after it. */
import { expect, test as setup } from '@playwright/test';
import { PASSWORD, SIGNED_IN, USER } from './fixtures.ts';

setup('sign in', async ({ page }) => {
	await page.goto('/login');
	await page.getByLabel('Name').fill(USER);
	await page.getByLabel('Password').fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();
	await page.context().storageState({ path: SIGNED_IN });
});
