/**
 * Administration: sources, schedules and runs, against the fake API's
 * /v1/admin (tests/fake-admin.ts). One file, so the tests run in order
 * against one fake.
 */
import { expect, test as base } from '@playwright/test';
import { FINGERPRINT } from './fake-admin.ts';

const test = base.extend<{ errors: string[] }>({
	errors: [
		async ({ page }, use) => {
			const errors: string[] = [];
			page.on('pageerror', (e) => errors.push(e.message));
			page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
			await use(errors);
			expect(errors, 'browser errors').toEqual([]);
		},
		{ auto: true }
	]
});

test('the side panel has an Administration section', async ({ page }) => {
	await page.goto('/');
	const nav = page.getByRole('navigation');
	await expect(nav.getByText('Administration')).toBeVisible();
	await nav.getByRole('link', { name: 'Schedules' }).click();
	await expect(page.getByRole('heading', { name: 'Schedules' })).toBeVisible();
	await page.goto('/admin');
	await expect(page).toHaveURL(/\/admin\/sources$/);
});

test('sources: listed without secrets, and switched on and off', async ({ page }) => {
	await page.goto('/admin/sources');
	const row = page.getByRole('row', { name: /work/ });
	await expect(row).toContainText('Rubrik account');
	await expect(row).toContainText('client_secret');
	const toggle = page.getByRole('switch', { name: 'Collect from work' });
	await expect(toggle).toHaveAttribute('aria-checked', 'false');
	await toggle.click();
	await expect(toggle).toHaveAttribute('aria-checked', 'true');
});

test('a NetBackup primary: fetch its CA, save, and watch the check', async ({ page }) => {
	await page.goto('/admin/sources');
	await page.getByRole('link', { name: 'Add a NetBackup primary' }).click();
	await page.getByLabel('Name in blt').fill('nbu-work-01');
	await page.getByLabel('Address').fill('nbu01.example.com');
	await page.getByLabel('API key').fill('typed-by-the-admin');

	await page.getByRole('button', { name: 'Fetch its CA' }).click();
	await expect(page.getByText(`SHA-256 ${FINGERPRINT}`).first()).toBeVisible();
	await expect(page.getByText("It verifies this primary's TLS certificate.")).toBeVisible();
	// what was typed is still there
	await expect(page.getByLabel('API key')).toHaveValue('typed-by-the-admin');
	await page.getByRole('button', { name: 'The fingerprint matches: use it' }).click();
	await expect(page.getByLabel('CA certificate (PEM)')).toHaveValue(/BEGIN CERTIFICATE/);

	await page.getByRole('button', { name: 'Save and check' }).click();
	await expect(page).toHaveURL(/\/admin\/sources\/netbackup\/nbu-work-01\?run=/);
	await expect(page.getByText('4 of 4 checks passed')).toBeVisible();
	await expect(page.getByText('Collecting:')).toContainText('off');
	await expect(page.getByText('typed-by-the-admin')).toHaveCount(0);

	await page.getByRole('button', { name: 'Turn on' }).click();
	await expect(page.getByRole('status')).toHaveText('Collection is on.');
	await expect(page.getByText('Collecting:')).toContainText('on');
});

test('a NetBackup primary with a bad name is refused with the reason', async ({ page }) => {
	await page.goto('/admin/sources/netbackup/new');
	await page.getByLabel('Name in blt').evaluate((input: HTMLInputElement) => input.removeAttribute('pattern'));
	await page.getByLabel('Name in blt').fill('Bad Name');
	await page.getByLabel('Address').fill('nbu02');
	await page.getByLabel('API key').fill('k');
	await page.getByRole('button', { name: 'Save and check' }).click();
	await expect(page.getByRole('alert')).toHaveText('name: Lower case letters, digits and dashes.');
	await expect(page.getByLabel('API key')).toHaveValue('k');
});

test('a NetBackup primary is removed only by typing its name', async ({ page }) => {
	await page.goto('/admin/sources/netbackup/nbu-work-01');
	await page.getByRole('button', { name: 'Remove' }).click();
	await expect(page.getByRole('alert')).toHaveText('Type nbu-work-01 to remove it.');
	await page.getByLabel('Type nbu-work-01 to remove it').fill('nbu-work-01');
	await page.getByRole('button', { name: 'Remove' }).click();
	await expect(page).toHaveURL(/\/admin\/sources$/);
	await expect(page.getByRole('row', { name: /nbu-work-01/ })).toHaveCount(0);
});

test('schedules: read as words, paused and resumed', async ({ page }) => {
	await page.goto('/admin/schedules');
	const row = page.getByRole('row', { name: /blt-collect-jobs/ });
	await expect(row).toContainText('hourly at :07');
	await expect(row).toContainText('Failed');
	await row.getByRole('button', { name: 'Pause' }).click();
	await expect(row).toContainText('paused');
	await row.getByRole('button', { name: 'Resume' }).click();
	await expect(row).not.toContainText('paused');
});

test('run now: asks for what it needs, then shows the run', async ({ page }) => {
	await page.goto('/admin/schedules');
	const row = page.getByRole('row', { name: /blt-collect-commcell/ });
	await row.getByRole('button', { name: 'Run now' }).click();
	await page.getByLabel('commcell').fill('cv-toaster');
	await page.getByRole('button', { name: 'Start the run' }).click();
	await expect(page).toHaveURL(/\/admin\/runs\/[0-9a-f-]{36}$/);
	await expect(page.getByText('Started with {"commcell":"cv-toaster"}')).toBeVisible();
});

test('runs: filtered by state, each with its log', async ({ page }) => {
	await page.goto('/admin/runs');
	await page.getByRole('link', { name: 'failed', exact: true }).click();
	await expect(page).toHaveURL(/state=failed/);
	const rows = page.getByRole('row');
	await expect(rows).toHaveCount(2); // the heading and calm-heron
	await page.getByRole('link', { name: 'calm-heron' }).click();
	await expect(page.getByText('cv-toaster: 401 from the CommCell')).toBeVisible();
	await expect(page.getByText('Flow run encountered an exception.')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Cancel the run' })).toHaveCount(0);
});

test('on a phone nothing spills sideways', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 800 });
	for (const path of ['/admin/sources', '/admin/schedules', '/admin/runs', '/admin/sources/netbackup/new']) {
		await page.goto(path);
		const sideways = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
		expect(sideways, path).toBe(false);
	}
});
