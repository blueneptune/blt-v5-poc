/**
 * A stand-in for blt's API, for the page tests: the few /v1 calls the UI
 * makes, answered from made-up jobs. The UI calls the API from its server
 * side, so the browser cannot mock it; this runs as a real server instead.
 *
 * Typed from the same docs/openapi.json as the UI (src/lib/api/schema.d.ts),
 * so when the API's answers change shape, `npm run check` fails here until
 * the fake follows.
 *
 *   node tests/fake-api.ts          (FAKE_API_PORT, default 4199)
 */
import { createServer, type ServerResponse } from 'node:http';
import type { components } from '../src/lib/api/schema';
import { admin } from './fake-admin.ts';
import { API_KEY, JOBS, SAMPLES, SOURCES, STORAGE, type Job } from './fixtures.ts';

type JobDetail = components['schemas']['CommonJobDetail'];
type SummaryRow = components['schemas']['SummaryRow'];

const PORT = Number(process.env.FAKE_API_PORT ?? 4199);

function matches(job: Job, q: URLSearchParams): boolean {
	const any = (name: string, value: string | null | undefined) => {
		const wanted = q.getAll(name);
		return wanted.length === 0 || wanted.includes(value ?? '');
	};
	const since = q.get('since');
	const until = q.get('until');
	const active = q.get('active');
	const asset = q.get('asset')?.toLowerCase();
	return (
		any('kind', job.kind) &&
		any('state', job.state) &&
		any('source_type', job.source_type) &&
		any('source', job.source_name) &&
		any('protection', job.protection) &&
		(active === null || String(job.is_active) === active) &&
		(!since || (job.started_at ?? '') >= since) &&
		(!until || (job.started_at ?? '') < until) &&
		(!asset || (job.asset_name ?? '').toLowerCase().includes(asset))
	);
}

function summary(jobs: Job[], groups: string[]): SummaryRow[] {
	const rows = new Map<string, SummaryRow>();
	for (const job of jobs) {
		const row: SummaryRow = { total: 0, states: {} };
		if (groups.includes('day')) row.day = job.started_at?.slice(0, 10) ?? null;
		if (groups.includes('source')) {
			row.source_type = job.source_type;
			row.source_name = job.source_name;
		}
		if (groups.includes('kind')) row.kind = job.kind;
		if (groups.includes('protection')) row.protection = job.protection;
		const key = JSON.stringify(row);
		const found = rows.get(key) ?? row;
		found.total += 1;
		found.states[job.state] = (found.states[job.state] ?? 0) + 1;
		rows.set(key, found);
	}
	return [...rows.values()];
}

function send(res: ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}) {
	res.writeHead(status, { 'content-type': 'application/json', ...headers });
	res.end(JSON.stringify(body));
}

createServer(async (req, res) => {
	const url = new URL(req.url ?? '/', 'http://fake');
	if (req.headers['x-api-key'] !== API_KEY) return send(res, 401, { detail: 'Invalid API key' });
	const q = url.searchParams;
	const path = url.pathname;

	let text = '';
	for await (const chunk of req) text += chunk;
	const answer = admin(req.method ?? 'GET', path, q, text ? JSON.parse(text) : null);
	if (answer) {
		if (answer[0] === 204) {
			res.writeHead(204);
			return res.end();
		}
		return send(res, answer[0], answer[1]);
	}

	if (path === '/v1/sources') return send(res, 200, SOURCES);
	if (path === '/v1/jobs/summary') return send(res, 200, summary(JOBS.filter((j) => matches(j, q)), q.getAll('group')));
	if (path === '/v1/jobs') {
		const found = JOBS.filter((j) => matches(j, q));
		const offset = Number(q.get('offset') ?? 0);
		const limit = Number(q.get('limit') ?? 100);
		return send(res, 200, found.slice(offset, offset + limit), {
			'X-Total-Count': String(found.length)
		});
	}
	if (path === '/v1/storage') return send(res, 200, STORAGE);
	const target = path.match(/^\/v1\/storage\/([0-9a-f-]{36})(\/samples)?$/);
	if (target) {
		const found = STORAGE.find((t) => t.id === target[1]);
		if (!found) return send(res, 404, { detail: 'storage target not found' });
		return send(res, 200, target[2] ? (found.id === STORAGE[0].id ? SAMPLES : []) : found);
	}
	const one = path.match(/^\/v1\/jobs\/([0-9a-f-]{36})$/);
	if (one) {
		const job = JOBS.find((j) => j.id === one[1]);
		if (!job) return send(res, 404, { detail: 'No such job' });
		// Shaped like a Commvault job row: blt's columns, and the product's own JSON in raw.
		const detail: JobDetail = {
			...job,
			product: {
				commcell_id: 1,
				job_id: Number(job.product_job_key),
				status: job.product_status,
				client_name: job.asset_name,
				storage_policy: job.protection,
				size_of_application: 5_368_709_120,
				elapsed_seconds: 3_725,
				is_aged: false,
				pending_reason: null,
				first_seen_at: job.first_seen_at,
				raw: {
					jobSummary: {
						jobId: Number(job.product_job_key),
						jobStartTime: 1_794_300_000,
						sizeOfApplication: 5_368_709_120,
						backupLevelName: 'Incremental',
						subclient: { clientName: job.asset_name, appName: 'File System', subclientName: 'default' },
						dataPaths: [
							{ mediaAgentName: 'ma01', libraryName: 'disk-lib-1', copyName: 'Primary' },
							{ mediaAgentName: 'ma02', libraryName: 'cloud-lib', copyName: 'Secondary' }
						],
						totalFailedFiles: 0,
						pendingReason: ''
					}
				}
			}
		};
		return send(res, 200, detail);
	}
	send(res, 404, { detail: 'Not Found' });
}).listen(PORT, '127.0.0.1', () => console.log(`fake blt API on http://127.0.0.1:${PORT}`));
