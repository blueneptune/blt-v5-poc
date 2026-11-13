/**
 * Every page loads and does its job, against the fake API's 130 jobs
 * (tests/fixtures.ts). Each test also fails on any browser error.
 */
import { expect, test as base, type Page } from '@playwright/test';
import { JOBS } from './fixtures.ts';

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

const backups = JOBS.filter((j) => j.kind === 'backup');
const everyJob = '/jobs?kind=all&window=all';

/** Where the table's box, its headings and the page itself are scrolled to. */
function layout(page: Page) {
	return page.evaluate(() => {
		const table = document.querySelector('table')!;
		const box = table.parentElement!;
		const root = document.documentElement;
		return {
			boxTop: box.getBoundingClientRect().top,
			headTop: table.tHead!.getBoundingClientRect().top,
			boxScrolled: box.scrollTop,
			pageScrolled: root.scrollTop,
			sideways: root.scrollWidth > root.clientWidth
		};
	});
}

test('overview: counts, sources and recent failures', async ({ page }) => {
	await page.goto('/');
	await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();
	await expect(page.getByText('Running now')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Recent failures' })).toBeVisible();
	await expect(page.getByText('cv-toaster').first()).toBeVisible();
	await expect(page.getByText('rsc-work').first()).toBeVisible();
});

test('jobs: backups of the last day by default, paged', async ({ page }) => {
	await page.goto('/jobs');
	await expect(page.getByText(`${backups.length} jobs`)).toBeVisible();
	await expect(page.locator('tbody tr')).toHaveCount(50);
	const kinds = await page.locator('tbody tr td:nth-child(5)').allTextContents();
	expect(new Set(kinds.map((k) => k.trim()))).toEqual(new Set(['backup']));

	await page.getByRole('link', { name: 'Older' }).click();
	await expect(page).toHaveURL(/page=2/);
	await expect(page.getByText(/Page 2 of/)).toBeVisible();
});

test('jobs: a state chip filters, and lives in the URL', async ({ page }) => {
	await page.goto(everyJob);
	await page.getByRole('link', { name: 'failed', exact: true }).click();
	await expect(page).toHaveURL(/state=failed/);
	const failed = JOBS.filter((j) => j.state === 'failed').length;
	await expect(page.getByText(`${failed} jobs`)).toBeVisible();
	await expect(page.locator('tbody tr')).toHaveCount(failed);

	await page.reload();
	await expect(page.getByText(`${failed} jobs`)).toBeVisible();
});

test('jobs: asset search', async ({ page }) => {
	await page.goto(everyJob);
	await page.getByLabel('Search by asset name').fill('lab-host-03');
	await page.getByLabel('Search by asset name').press('Enter');
	await expect(page).toHaveURL(/asset=lab-host-03/);
	const expected = JOBS.filter((j) => j.asset_name === 'lab-host-03').length;
	await expect(page.locator('tbody tr')).toHaveCount(expected);
});

test('jobs: pinned, the filters and column headings stay while the table scrolls', async ({ page }) => {
	await page.setViewportSize({ width: 1400, height: 700 });
	await page.goto(everyJob);
	await expect(page.getByRole('button', { name: /scroll away/ })).toHaveAttribute('aria-pressed', 'true');

	await page.locator('tbody').hover();
	await page.mouse.wheel(0, 1500);
	await expect.poll(async () => (await layout(page)).boxScrolled).toBeGreaterThan(0);
	const pinned = await layout(page);
	expect(pinned.pageScrolled).toBe(0);
	expect(Math.abs(pinned.headTop - pinned.boxTop)).toBeLessThanOrEqual(2);
	expect(pinned.sideways).toBe(false);
	await expect(page.getByRole('heading', { name: 'Jobs' })).toBeInViewport();
	await expect(page.getByText(/\d+ jobs$/)).toBeInViewport();
});

test('jobs: unpinned, everything scrolls away, and that is remembered', async ({ page }) => {
	await page.setViewportSize({ width: 1400, height: 700 });
	await page.goto(everyJob);
	await page.getByRole('button', { name: /scroll away/ }).click();
	await page.reload();
	await expect(page.getByRole('button', { name: /Keep the filters/ })).toHaveAttribute('aria-pressed', 'false');

	await page.mouse.wheel(0, 1500);
	await expect.poll(async () => (await layout(page)).pageScrolled).toBeGreaterThan(0);
	await expect(page.getByRole('heading', { name: 'Jobs' })).not.toBeInViewport();
});

test('jobs: columns are chosen, and the choice is remembered', async ({ page }) => {
	await page.setViewportSize({ width: 1400, height: 800 });
	await page.goto(everyJob);
	const heading = (name: string) => page.getByRole('columnheader', { name, exact: true });
	await expect(heading('Job ID')).toHaveCount(0);

	await page.getByRole('button', { name: 'Columns' }).click();
	await page.getByRole('checkbox', { name: /^Job ID/ }).check();
	await page.getByRole('checkbox', { name: /^Kind/ }).uncheck();
	await page.keyboard.press('Escape');
	await expect(page.getByRole('group', { name: 'Columns shown' })).toBeHidden();
	await expect(heading('Job ID')).toHaveCount(1);
	await expect(heading('Kind')).toHaveCount(0);
	// Its column shows each job's own id.
	const first = page.locator('tbody tr').first();
	await expect(first.locator("td").nth(4)).toHaveText(/\S/);

	await page.reload();
	await expect(heading('Job ID')).toHaveCount(1);
	await expect(heading('Kind')).toHaveCount(0);

	await page.getByRole('button', { name: 'Columns' }).click();
	await page.getByRole('button', { name: 'Back to the usual columns and widths' }).click();
	await expect(heading('Job ID')).toHaveCount(0);
	await expect(heading('Kind')).toHaveCount(1);
});

test('jobs: a column is resized by dragging or with the keys, and remembered', async ({ page }) => {
	await page.setViewportSize({ width: 1400, height: 800 });
	await page.goto(everyJob);
	const asset = page.getByRole('columnheader', { name: 'Asset', exact: true });
	const edge = page.getByRole('separator', { name: 'Width of Asset' });
	const width = async () => Math.round((await asset.boundingBox())!.width);
	expect(await width()).toBe(256);

	const box = (await edge.boundingBox())!;
	await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
	await page.mouse.down();
	await page.mouse.move(box.x + 120, box.y + box.height / 2, { steps: 5 });
	await page.mouse.up();
	await expect.poll(width).toBeGreaterThan(360);
	// Dragging a heading's edge does not open the job underneath.
	await expect(page).toHaveURL(/\/jobs\?/);

	await edge.focus();
	const dragged = await width();
	await page.keyboard.press('ArrowLeft');
	await expect.poll(width).toBe(dragged - 16);
	await expect(edge).toHaveAttribute('aria-valuenow', String(dragged - 16));

	await page.reload();
	await expect.poll(width).toBe(dragged - 16);
	await page.getByRole('separator', { name: 'Width of Asset' }).dblclick();
	await expect.poll(width).toBe(256);
});

test('jobs: narrower than the table, it scrolls sideways inside its box', async ({ page }) => {
	await page.setViewportSize({ width: 1024, height: 700 });
	await page.goto(everyJob);
	const { sideways } = await layout(page);
	expect(sideways).toBe(false);
});

test('jobs: on a phone nothing is pinned and nothing spills sideways', async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.goto(everyJob);
	await expect(page.getByRole('button', { name: /scroll away/ })).toBeHidden();
	await page.mouse.wheel(0, 1500);
	await expect.poll(async () => (await layout(page)).pageScrolled).toBeGreaterThan(0);
	expect((await layout(page)).sideways).toBe(false);
});

test('jobs: by protection', async ({ page }) => {
	await page.goto(everyJob);
	await page.getByRole('link', { name: 'By protection' }).click();
	await expect(page).toHaveURL(/view=protection/);
	await expect(page.getByRole('columnheader', { name: 'Outcome' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Gold' }).first()).toBeVisible();
	await expect(page.getByText('none').first()).toBeVisible();
});

test('a job: from the list to its page', async ({ page }) => {
	const job = JOBS[0];
	await page.goto(everyJob);
	await page.locator('tbody tr').first().click();
	await expect(page).toHaveURL(`/jobs/${job.id}`);
	await expect(page.getByRole('heading', { name: job.asset_name! })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Collection' })).toBeVisible();
	await expect(page.getByText(`${job.source_name}'s own record`)).toBeVisible();
	// The product's record laid out, the JSON still underneath.
	await expect(page.getByText('Size of application').first()).toBeVisible();
	await expect(page.getByText('5.0 GiB (5,368,709,120)').first()).toBeVisible();
	await expect(page.getByText('1h 2m')).toBeVisible();
	await expect(page.getByRole('heading', { name: 'As Commvault reports it' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'Job summary' })).toBeVisible();
	await expect(page.getByRole('heading', { name: /Data paths \(2\)/ })).toBeVisible();
	await expect(page.getByRole('cell', { name: 'cloud-lib' })).toBeVisible();
	await expect(page.getByText('Commcell ID')).toHaveCount(0);
	await page.getByText('Raw record (JSON)').click();
	await expect(page.locator('pre')).toContainText('"mediaAgentName": "ma02"');
});

test('a job that does not exist is a 404 page', async ({ page, errors }) => {
	const response = await page.goto('/jobs/00000000-0000-4000-8000-ffffffffffff');
	expect(response?.status()).toBe(404);
	await expect(page.getByRole('heading', { name: /No such job/ })).toBeVisible();
	// The browser reports the 404 it was sent here to get: the one error expected.
	expect(errors).toEqual([expect.stringContaining('404')]);
	errors.length = 0;
});

test('sources: each source with its week', async ({ page }) => {
	await page.goto('/sources');
	await expect(page.getByRole('heading', { name: 'Sources' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'cv-toaster' })).toBeVisible();
	await expect(page.getByRole('heading', { name: 'rsc-work' })).toBeVisible();
});

test('storage: totals, each target with how full it is', async ({ page }) => {
	await page.goto('/storage');
	await expect(page.getByRole('heading', { name: 'Storage', exact: true })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Storage' })).toHaveAttribute('aria-current', 'page');
	await expect(page.getByText('Need attention')).toBeVisible();
	await expect(page.getByRole('meter', { name: /97% used, Nearly full/ })).toBeVisible();
	await expect(page.getByRole('meter', { name: /capacity unknown/ })).toBeVisible();
	await expect(page.getByRole('row', { name: /pool-steady/ })).toContainText('26 days');
});

test("storage: where NetBackup's data goes, and how full each pool is", async ({ page }) => {
	await page.goto('/storage');
	const flows = page.getByRole('region', { name: "Where NetBackup's data goes" });
	await expect(flows.getByRole('img', { name: /2 flows into 1 pools/ })).toBeVisible();
	await expect(flows.getByRole('list', { name: 'Legend' })).toContainText('Replicated to another primary');
	await expect(flows.getByText(/msdp-a/).first()).toBeVisible();
	await expect(flows.getByText('Nearly full').first()).toBeVisible();
	await expect(flows.getByText(/2 replication jobs/)).toBeVisible();
	await expect(flows.getByText(/Nothing written in this window: .*idle-pool/)).toBeVisible();
	await flows.getByText('Show as a table').click();
	await expect(flows.getByRole('row', { name: /Backups on nbu-a msdp-a Backups 12 TiB 40/ })).toBeVisible();
	await flows.getByRole('link', { name: '30 days' }).click();
	await expect(page).toHaveURL(/flow=30/);
});

test('storage: by MediaAgent, a shared pool is under each', async ({ page }) => {
	await page.goto('/storage');
	await page.getByRole('link', { name: 'MediaAgent / media server' }).click();
	await expect(page).toHaveURL(/by=host/);
	await expect(page.getByRole('heading', { name: 'ma01' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'pool-steady' })).toHaveCount(2);
});

test('a storage target: its use over time, with a readout', async ({ page }) => {
	await page.goto('/storage');
	await page.getByRole('link', { name: 'pool-steady' }).click();
	await expect(page.getByRole('heading', { name: 'pool-steady' })).toBeVisible();
	await expect(page.getByText('7.64:1')).toBeVisible();
	const chart = page.getByRole('img', { name: 'Used space over time' });
	await expect(chart).toBeVisible();
	const box = (await chart.boundingBox())!;
	await page.mouse.move(box.x + box.width * 0.9, box.y + box.height / 2);
	await expect(page.getByText(/capacity$/).first()).toBeVisible();
	await expect(page.getByText('mount-01')).toBeVisible();
});

test('a storage target that does not exist is a 404 page', async ({ page, errors }) => {
	const response = await page.goto('/storage/00000000-0000-4000-8000-00000000ffff');
	expect(response?.status()).toBe(404);
	await expect(page.getByText('No such storage target')).toBeVisible();
	// the browser logs the 404 itself
	errors.length = 0;
});

test('light and dark both render', async ({ page }) => {
	await page.emulateMedia({ colorScheme: 'dark' });
	await page.goto('/jobs');
	await expect(page.locator('html')).toHaveClass(/dark/);
	await page.emulateMedia({ colorScheme: 'light' });
	await page.goto('/jobs');
	await expect(page.locator('html')).not.toHaveClass(/dark/);
});
