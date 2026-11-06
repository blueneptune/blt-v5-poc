/**
 * The fake API's /v1/admin: sources, deployments and runs, kept in
 * memory so the page tests can add a primary, pause a schedule and watch
 * a run. A run started here is already over, with the log its real flow
 * would write (blt.flows.netbackup).
 */
import type { components } from '../src/lib/api/schema';

type Source = components['schemas']['SourceBlockOut'];
type Deployment = components['schemas']['DeploymentOut'];
type Run = components['schemas']['FlowRunOut'];
type Line = components['schemas']['LogLine'];

let counter = 0;
const id = (kind: number) => `00000000-0000-4000-9${kind}00-${(++counter).toString(16).padStart(12, '0')}`;
const now = Date.now();
const iso = (minutesAgo: number) => new Date(now - minutesAgo * 60_000).toISOString();

export const PEM = '-----BEGIN CERTIFICATE-----\nMIIBfakeNetBackupCA\n-----END CERTIFICATE-----';
export const FINGERPRINT = 'AB:CD:EF:01';

const sources: Source[] = [
	{
		type: 'commvault',
		name: 'cv-toaster',
		block_id: id(1),
		collect: true,
		address: 'https://cv-toaster/commandcenter/api',
		verify_tls: true,
		has_ca_certificate: null,
		secrets_set: ['access_token', 'refresh_token'],
		updated: iso(60)
	},
	{
		type: 'rubrik',
		name: 'work',
		block_id: id(1),
		collect: false,
		address: 'https://example.my.rubrik.com/api/client_token',
		verify_tls: true,
		has_ca_certificate: null,
		secrets_set: ['client_secret'],
		updated: iso(600)
	}
];

const schema = (properties: Record<string, unknown>, required: string[] = []) => ({ properties, required });

const deployments: Deployment[] = [
	{
		id: id(2),
		name: 'hourly',
		flow_name: 'blt-collect-jobs',
		description: 'Jobs from every CommCell block.',
		paused: false,
		schedules: [{ cron: '7 * * * *', timezone: 'UTC', active: true, interval_seconds: null }],
		parameters: {},
		parameter_schema: schema({ wait_minutes: { type: 'number', title: 'wait_minutes', default: 50 } })
	},
	{
		id: id(2),
		name: 'per-commcell',
		flow_name: 'blt-collect-commcell',
		description: 'One CommCell.',
		paused: false,
		schedules: [],
		parameters: {},
		parameter_schema: schema({ commcell: { type: 'string', title: 'commcell' } }, ['commcell'])
	},
	{
		id: id(2),
		name: 'by-hand',
		flow_name: 'blt-netbackup-check',
		description: 'Checks one primary.',
		paused: false,
		schedules: [],
		parameters: {},
		parameter_schema: schema({ primary: { type: 'string', title: 'primary' } }, ['primary'])
	},
	{
		id: id(2),
		name: 'by-hand',
		flow_name: 'blt-netbackup-fetch-ca',
		description: 'Fetches a CA certificate.',
		paused: false,
		schedules: [],
		parameters: {},
		parameter_schema: schema({ url: { type: 'string', title: 'url' } }, ['url'])
	}
];

const runs: Run[] = [];
const logs = new Map<string, Line[]>();

function addRun(deployment: Deployment, parameters: Record<string, unknown>, name: string, lines: [string, string][], state = 'COMPLETED', minutesAgo = 0): Run {
	const run: Run = {
		id: id(3),
		name,
		flow_name: deployment.flow_name,
		deployment_id: deployment.id,
		deployment_name: deployment.name,
		state_type: state,
		state_name: state.charAt(0) + state.slice(1).toLowerCase(),
		message: state === 'FAILED' ? 'Flow run encountered an exception.' : null,
		parameters,
		auto_scheduled: false,
		expected_start_time: iso(minutesAgo + 0.2),
		start_time: iso(minutesAgo + 0.1),
		end_time: iso(minutesAgo),
		total_run_time: 6
	};
	runs.unshift(run);
	logs.set(
		run.id,
		lines.map(([level, message], i) => ({ timestamp: new Date(now - minutesAgo * 60_000 + i * 1000).toISOString(), level, message }))
	);
	return run;
}

// Something to see on the runs page from the start.
addRun(deployments[0], {}, 'brave-otter', [['INFO', 'cv-toaster: 12 jobs']], 'COMPLETED', 53);
addRun(deployments[0], {}, 'calm-heron', [['ERROR', 'cv-toaster: 401 from the CommCell']], 'FAILED', 113);

const flow = (name: string) => deployments.find((d) => d.flow_name === name)!;

function startCheck(primary: string): Run {
	return addRun(flow('blt-netbackup-check'), { primary }, `check-${primary}`, [
		['INFO', `Checking NetBackup ${primary}`],
		['INFO', 'OK   sign in and server: nbu01, NetBackup 10.5.0.1, API version 12.0'],
		['INFO', 'OK   storage (disk pools, storage units): 2 storage targets (MSDP, MSDP-C)'],
		['INFO', 'OK   SLP images (for the SLP backlog): readable'],
		['INFO', 'OK   jobs: readable (42 jobs)'],
		['INFO', '4 of 4 checks passed']
	]);
}

type Answer = [number, unknown];

export function admin(method: string, path: string, q: URLSearchParams, body: any): Answer | null {
	if (!path.startsWith('/v1/admin/')) return null;
	const rest = path.slice('/v1/admin'.length);

	if (rest === '/sources' && method === 'GET') return [200, sources];
	if (rest === '/sources/netbackup' && method === 'POST') {
		if (!/^[a-z0-9][a-z0-9-]*$/.test(body.name)) return [422, { detail: [{ loc: ['body', 'name'], msg: 'Value error, Lower case letters, digits and dashes.' }] }];
		if (sources.some((s) => s.type === 'netbackup' && s.name === body.name)) return [409, { detail: `A NetBackup source '${body.name}' exists` }];
		const host = String(body.url).replace(/^https:\/\//, '').split(/[:/]/)[0];
		const added: Source = {
			type: 'netbackup',
			name: body.name,
			block_id: id(1),
			collect: false,
			address: `https://${host}:1556/netbackup`,
			verify_tls: true,
			has_ca_certificate: !!body.ca_certificate,
			secrets_set: body.api_key ? ['api_key'] : [],
			updated: new Date().toISOString()
		};
		sources.push(added);
		return [201, added];
	}
	const source = rest.match(/^\/sources\/(commvault|rubrik|netbackup)\/([^/]+)(\/check)?$/);
	if (source) {
		const found = sources.find((s) => s.type === source[1] && s.name === source[2]);
		if (!found) return [404, { detail: `No ${source[1]} source '${source[2]}'` }];
		if (source[3]) return [201, startCheck(found.name)];
		if (method === 'PATCH') {
			if (body.collect !== undefined && body.collect !== null) found.collect = body.collect;
			if (body.ca_certificate === '') found.has_ca_certificate = false;
			else if (body.ca_certificate) found.has_ca_certificate = true;
			found.updated = new Date().toISOString();
			return [200, found];
		}
		if (method === 'DELETE') {
			sources.splice(sources.indexOf(found), 1);
			return [204, null];
		}
	}
	if (rest === '/netbackup/ca-certificate' && method === 'POST') {
		const url = String(body.url).includes('://') ? body.url : `https://${body.url}:1556/netbackup`;
		return [
			201,
			addRun(flow('blt-netbackup-fetch-ca'), { url }, 'fetch-ca', [
				['INFO', `${url} gave 1 CA certificate(s)`],
				['INFO', `CA certificate 1: CN=nbatd,OU=root@nbu01,O=vx - SHA-256 ${FINGERPRINT}`],
				['INFO', PEM],
				['INFO', "CA certificate 1 verifies this primary's TLS certificate."]
			])
		];
	}

	if (rest === '/deployments') {
		return [
			200,
			deployments.map((d) => {
				const last = runs.find((r) => r.deployment_id === d.id && r.state_type !== 'SCHEDULED');
				return {
					...d,
					last_run: last ?? null,
					next_run: d.schedules.length ? { id: id(3), name: 'next', state_type: 'SCHEDULED', state_name: 'Scheduled', expected_start_time: iso(-30) } : null
				};
			})
		];
	}
	const deployment = rest.match(/^\/deployments\/([0-9a-f-]{36})\/(pause|resume|runs)$/);
	if (deployment) {
		const found = deployments.find((d) => d.id === deployment[1]);
		if (!found) return [404, { detail: 'No deployment' }];
		if (deployment[2] === 'runs') return [201, addRun(found, body.parameters ?? {}, `run-${counter}`, [['INFO', `Started with ${JSON.stringify(body.parameters ?? {})}`]])];
		found.paused = deployment[2] === 'pause';
		return [200, { paused: found.paused }];
	}

	if (rest === '/runs') {
		const states = q.getAll('state');
		const wanted = q.get('deployment_id');
		const found = runs.filter((r) => (!states.length || states.includes(r.state_type ?? '')) && (!wanted || r.deployment_id === wanted));
		return [200, found];
	}
	const run = rest.match(/^\/runs\/([0-9a-f-]{36})(\/cancel)?$/);
	if (run) {
		const found = runs.find((r) => r.id === run[1]);
		if (!found) return [404, { detail: 'No flow run' }];
		const offset = Number(q.get('log_offset') ?? 0);
		const lines = logs.get(found.id) ?? [];
		return [200, { ...found, logs: lines.slice(offset), logs_truncated: false }];
	}
	return [404, { detail: 'Not Found' }];
}
