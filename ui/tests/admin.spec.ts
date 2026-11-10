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

test('the side panel has an Administration group, open on its pages', async ({ page }) => {
	await page.goto('/');
	const nav = page.getByRole('navigation');
	const group = nav.getByRole('button', { name: 'Administration' });
	await expect(group).toHaveAttribute('aria-expanded', 'false');
	await expect(nav.getByRole('link', { name: 'Schedules' })).toHaveCount(0);
	await group.click();
	await expect(group).toHaveAttribute('aria-expanded', 'true');
	await nav.getByRole('link', { name: 'Schedules' }).click();
	await expect(page.getByRole('heading', { name: 'Schedules' })).toBeVisible();
	// Open by itself on its pages, and foldable there.
	await expect(group).toHaveAttribute('aria-expanded', 'true');
	await expect(nav.getByRole('link', { name: 'Schedules' })).toHaveAttribute('aria-current', 'page');
	await group.click();
	await expect(nav.getByRole('link', { name: 'Runs' })).toHaveCount(0);
	await page.goto('/admin');
	await expect(page).toHaveURL(/\/admin\/sources$/);
	await expect(nav.getByRole('link', { name: 'Sources' }).last()).toHaveAttribute('aria-current', 'page');
});

test('on a phone: one Admin link, and tabs for its pages', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 800 });
	await page.goto('/');
	const side = page.getByRole('navigation').first();
	await expect(side.getByRole('link', { name: 'Admin', exact: true })).toBeVisible();
	await expect(side.getByRole('link', { name: 'Schedules' })).toHaveCount(0);
	await side.getByRole('link', { name: 'Admin', exact: true }).click();
	const tabs = page.getByRole('navigation', { name: 'Administration' });
	await expect(tabs.getByRole('link', { name: 'Sources' })).toHaveAttribute('aria-current', 'page');
	await tabs.getByRole('link', { name: 'Runs' }).click();
	await expect(page.getByRole('heading', { name: 'Runs' })).toBeVisible();
	await expect(tabs.getByRole('link', { name: 'Runs' })).toHaveAttribute('aria-current', 'page');
});

test('sources: listed without secrets, and switched on and off', async ({ page }) => {
	await page.goto('/admin/sources');
	const row = page.getByRole('row', { name: /^work / });
	await expect(row).toContainText('Rubrik account');
	await expect(row).toContainText('client_secret');
	const toggle = page.getByRole('switch', { name: 'Collect from work' });
	await expect(toggle).toHaveAttribute('aria-checked', 'false');
	await toggle.click();
	await expect(toggle).toHaveAttribute('aria-checked', 'true');
});

test('collectors: each one shown with whether it is there, and a source moved to one', async ({ page }) => {
	await page.goto('/admin/sources');
	const panel = page.getByRole('region', { name: 'Collectors' });
	await expect(panel.getByRole('listitem').filter({ hasText: 'blt’s own worker' })).toContainText('online');
	await expect(panel.getByRole('listitem').filter({ hasText: 'uk' })).toContainText('offline');

	const select = page.getByLabel('Collector for cv-toaster');
	await expect(select).toHaveValue('');
	await select.selectOption('omaha');
	await expect(page.getByLabel('Collector for cv-toaster')).toHaveValue('omaha');
	await expect(panel.getByRole('listitem').filter({ hasText: 'omaha' })).toContainText('1 source');

	await page.getByLabel('Collector for cv-toaster').selectOption('');
	await expect(page.getByLabel('Collector for cv-toaster')).toHaveValue('');
});

test('a NetBackup primary added for a remote collector keeps it', async ({ page }) => {
	await page.goto('/admin/sources/netbackup/new');
	await page.getByLabel('Collected by').selectOption('uk');
	await page.getByLabel('Name in blt').fill('nbu-uk-01');
	await page.getByLabel('Address').fill('nbu-uk.example.com');
	await page.getByLabel('API key').fill('typed-by-the-admin');
	await page.getByRole('button', { name: 'Save and check' }).click();
	await expect(page).toHaveURL(/\/admin\/sources\/netbackup\/nbu-uk-01/);
	await expect(page.getByLabel('Collected by')).toHaveValue('uk');
	await page.getByLabel('Collected by').selectOption('');
	await page.getByRole('button', { name: 'Set' }).click();
	await expect(page.getByRole('status')).toContainText('the check runs from its collector');
	await expect(page.getByLabel('Collected by')).toHaveValue('');
});

test('a NetBackup primary: fetch its CA, save, and watch the check', async ({ page }) => {
	await page.goto('/admin/sources');
	await page.getByLabel('Kind of source to add').selectOption('NetBackup primary');
	await expect(page).toHaveURL(/\/admin\/sources\/netbackup\/new$/);
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

test('a NetBackup primary that answers only on 443 is found there', async ({ page }) => {
	await page.goto('/admin/sources/netbackup/new');
	await page.getByLabel('Address').fill('nbu443.example.com');
	await page.getByRole('button', { name: 'Fetch its CA' }).click();
	await expect(page.getByText('Port 1556 did not answer, 443 did')).toBeVisible();
	await expect(page.getByLabel('Address')).toHaveValue('https://nbu443.example.com:443/netbackup');
	await expect(page.getByText(`SHA-256 ${FINGERPRINT}`).first()).toBeVisible();
});

test('a NetBackup primary with a 2017 CA: legacy key sizes, turned on and off', async ({ page }) => {
	await page.goto('/admin/sources/netbackup/new');
	await page.getByLabel('Name in blt').fill('nbu-2017');
	await page.getByLabel('Address').fill('nbu2017.example.com');
	await page.getByLabel('API key').fill('k');
	await page.getByRole('button', { name: 'Fetch its CA' }).click();
	await expect(page.getByText('Using it turns that on')).toBeVisible();
	await expect(page.getByLabel('Accept legacy key sizes')).not.toBeChecked();
	await page.getByRole('button', { name: 'The fingerprint matches: use it' }).click();
	await expect(page.getByLabel('Accept legacy key sizes')).toBeChecked();

	await page.getByRole('button', { name: 'Save and check' }).click();
	await expect(page).toHaveURL(/\/admin\/sources\/netbackup\/nbu-2017/);
	await expect(page.getByText('accepted', { exact: true })).toBeVisible();
	await page.getByRole('button', { name: 'Stop accepting' }).click();
	await expect(page.getByRole('status')).toHaveText(/no longer accepted/);
	await expect(page.getByRole('button', { name: 'Accept', exact: true })).toBeVisible();
	await page.getByRole('button', { name: 'Accept', exact: true }).click();

	await page.goto('/admin/sources');
	await expect(page.getByRole('row', { name: /nbu-2017/ })).toContainText('legacy TLS keys');
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

test('add a source: a choice of product, also without the drop-down', async ({ page }) => {
	await page.goto('/admin/sources/new');
	await expect(page.getByRole('heading', { name: 'Add a source' })).toBeVisible();
	await page.getByRole('link', { name: /Rubrik account/ }).click();
	await expect(page).toHaveURL(/\/admin\/sources\/rubrik\/new$/);
	// the drop-down's form, as it is sent without JavaScript
	await page.goto('/admin/sources/new?type=commvault');
	await expect(page).toHaveURL(/\/admin\/sources\/commvault\/new$/);
});

test('a CommCell: added with a password, checked, its sign-in changed to a token', async ({ page }) => {
	await page.goto('/admin/sources');
	await page.getByLabel('Kind of source to add').selectOption('Commvault CommCell');
	await expect(page).toHaveURL(/\/admin\/sources\/commvault\/new$/);
	await page.getByLabel('Name in blt').fill('cv-prod-01');
	await page.getByLabel('Address').fill('cs01.example.com');
	await page.getByLabel('User name', { exact: true }).fill('blt-reader');
	await page.getByLabel('Password', { exact: true }).fill('typed-by-the-admin');
	await page.getByRole('button', { name: 'Save and check' }).click();

	await expect(page).toHaveURL(/\/admin\/sources\/commvault\/cv-prod-01\?run=/);
	await expect(page.getByText('3 of 3 checks passed')).toBeVisible();
	await expect(page.getByText('as blt-reader')).toBeVisible();
	await expect(page.getByText('Collecting:')).toContainText('off');
	await expect(page.getByText('typed-by-the-admin')).toHaveCount(0);

	await page.getByRole('radio', { name: 'Access token' }).check();
	await page.getByLabel('New access token').fill('AT');
	await page.getByLabel('Its refresh token').fill('RT');
	await page.getByRole('button', { name: 'Save changes' }).click();
	await expect(page.getByRole('status')).toHaveText('Saved. Check it again to be sure.');
	await expect(page.getByText('with an access token, renewed by blt')).toBeVisible();

	await page.goto('/admin/sources');
	await expect(page.getByRole('row', { name: /cv-prod-01/ })).toContainText('Commvault CommCell');
});

test('a Commvault Cloud tenant needs no address, and is named as one', async ({ page }) => {
	await page.goto('/admin/sources/commvault/new');
	await page.getByLabel('Name in blt').fill('cv-cloud');
	await page.getByRole('radio', { name: /Commvault Cloud/ }).check();
	await expect(page.getByLabel('Address')).toHaveAttribute('placeholder', 'https://api.metallic.io');
	await page.getByRole('radio', { name: /Access token/ }).check();
	await page.getByLabel('Access token', { exact: true }).fill('AT');
	await page.getByRole('button', { name: 'Save and check' }).click();
	await expect(page).toHaveURL(/\/admin\/sources\/commvault\/cv-cloud\?run=/);
	await expect(page.getByText('Commvault Cloud tenant')).toBeVisible();
	await expect(page.getByText('no refresh token: it will expire')).toBeVisible();
});

const serviceAccount = (uri = 'https://acme.my.rubrik.com/api/client_token') => ({
	name: 'blt-reader.json',
	mimeType: 'application/json',
	buffer: Buffer.from(
		JSON.stringify({ client_id: 'client|x', client_secret: 'rsc-secret-123', access_token_uri: uri, name: 'blt-reader' })
	)
});

test('a Rubrik account: added from its service account file, and checked', async ({ page }) => {
	await page.goto('/admin/sources');
	await page.getByLabel('Kind of source to add').selectOption('Rubrik account');
	await page.getByLabel('Name in blt').fill('rsc-acme');
	await page.getByLabel('Service account file').setInputFiles(serviceAccount());
	await page.getByRole('button', { name: 'Save and check' }).click();

	await expect(page).toHaveURL(/\/admin\/sources\/rubrik\/rsc-acme\?run=/);
	await expect(page.getByText('3 of 3 checks passed')).toBeVisible();
	await expect(page.getByText('blt-reader · secret set')).toBeVisible();
	await expect(page.getByText('rsc-secret-123')).toHaveCount(0);
});

test('a file that is not a service account is refused, saying so', async ({ page }) => {
	await page.goto('/admin/sources/rubrik/new');
	await page.getByLabel('Name in blt').fill('rsc-wrong');
	await page
		.getByLabel('Service account file')
		.setInputFiles({ name: 'x.json', mimeType: 'application/json', buffer: Buffer.from('{"hello": 1}') });
	await page.getByRole('button', { name: 'Save and check' }).click();
	await expect(page.getByRole('alert')).toContainText('is it an RSC service account file?');

	await page.getByLabel('Service account file').setInputFiles(serviceAccount('https://acme.my.rubrik.com/api/graphql'));
	await page.getByRole('button', { name: 'Save and check' }).click();
	await expect(page.getByRole('alert')).toContainText('should end with /api/client_token');
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

test('stuck runs: shown, and cleared only once confirmed', async ({ page }) => {
	await page.goto('/admin/schedules');
	const panel = page.getByRole('region', { name: 'Run health' });
	await expect(panel).toContainText('Runs are stuck');
	await expect(panel).toContainText('4 of 4 collection slots are taken, but only 0 runs are collecting');
	await expect(panel).toContainText('3 runs have been running for over 2 h');
	await expect(panel.getByRole('row', { name: /netbackup-jobs-nbu03/ })).toContainText('7 h');

	await panel.getByRole('button', { name: 'Clear stuck runs…' }).click();
	await expect(panel).toContainText('This cancels the 3 runs above');
	await panel.getByRole('button', { name: 'Keep them' }).click();
	await expect(panel).toContainText('Runs are stuck');

	await panel.getByRole('button', { name: 'Clear stuck runs…' }).click();
	await panel.getByRole('button', { name: 'Yes, clear them' }).click();
	await expect(panel.getByRole('status')).toHaveText('Cancelled 3 runs; slots taken 4 → 0.');
	await expect(panel).toContainText('Runs are getting through: 0 of 4 collection slots taken, 0 collecting.');
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
	for (const path of [
		'/admin/sources',
		'/admin/schedules',
		'/admin/runs',
		'/admin/sources/netbackup/new',
		'/admin/sources/commvault/new',
		'/admin/sources/rubrik/new',
		'/admin/sources/new'
	]) {
		await page.goto(path);
		const sideways = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
		expect(sideways, path).toBe(false);
	}
});
