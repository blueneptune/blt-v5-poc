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
type Collector = components['schemas']['CollectorOut'];

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

// blt's own worker, and remote collectors for two regions: one there,
// one whose tunnel is down.
const queues: { name: string; status: Collector['status']; last_polled: string | null }[] = [
	{ name: 'default', status: 'online', last_polled: iso(0) },
	{ name: 'omaha', status: 'online', last_polled: iso(0) },
	{ name: 'uk', status: 'offline', last_polled: iso(180) }
];

function collectors(): Collector[] {
	return queues.map((q) => ({
		...q,
		own: q.name === 'default',
		sources: sources
			.filter((s) => (s.collector ?? 'default') === q.name)
			.map((s) => `${s.type}/${s.name}`)
	}));
}

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
		flow_name: 'blt-commvault-check',
		description: 'Checks one CommCell.',
		paused: false,
		schedules: [],
		parameters: {},
		parameter_schema: schema({ commcell: { type: 'string', title: 'commcell' } }, ['commcell'])
	},
	{
		id: id(2),
		name: 'by-hand',
		flow_name: 'blt-rubrik-check',
		description: 'Checks one Rubrik account.',
		paused: false,
		schedules: [],
		parameters: {},
		parameter_schema: schema({ account: { type: 'string', title: 'account' } }, ['account'])
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

function startSourceCheck(source: Source): Run {
	if (source.type === 'commvault')
		return addRun(flow('blt-commvault-check'), { commcell: source.name }, `check-${source.name}`, [
			['INFO', `Checking CommCell ${source.name} at ${source.address}`],
			['INFO', 'OK   sign in: CommServe cs01 version 11.36'],
			['INFO', 'OK   jobs: readable (12 in the last 24 hours)'],
			['INFO', 'OK   storage (pools, libraries): 3 storage targets'],
			['INFO', '3 of 3 checks passed']
		]);
	if (source.type === 'rubrik')
		return addRun(flow('blt-rubrik-check'), { account: source.name }, `check-${source.name}`, [
			['INFO', `Checking Rubrik account ${source.name} as ${source.login ?? 'its service account'}`],
			['INFO', 'OK   sign in and jobs: signed in; jobs readable (some lately)'],
			['INFO', 'OK   inventory: readable (5 objects in a first look)'],
			['INFO', 'OK   SLA domains: readable (4 SLA domains in a first page)'],
			['INFO', '3 of 3 checks passed']
		]);
	return startCheck(source.name);
}

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

// Run health: as on the desktop on 2026-11-09 - every slot held by runs
// that are gone, and runs stuck waiting - until it is cleared.
const stuckFlow = 'blt-netbackup-jobs-primary';
const health = {
	slots: { name: 'blt-commcell-runs', limit: 4, active: 4 },
	stuck: [5, 6, 7].map((hours, i) => ({
		id: id(3),
		name: `netbackup-jobs-nbu0${i + 1}`,
		flow_name: stuckFlow,
		deployment_id: null,
		deployment_name: null,
		state_type: 'RUNNING',
		state_name: 'Running',
		message: null,
		parameters: { primary: `nbu0${i + 1}` },
		auto_scheduled: true,
		expected_start_time: iso(hours * 60),
		start_time: iso(hours * 60),
		end_time: null,
		total_run_time: 0,
		stuck_minutes: hours * 60
	})),
	stuck_after_minutes: 120
};

function healthOut() {
	const collecting = 0;
	const leaked = Math.max(0, health.slots.active - collecting);
	return { ...health, collecting, leaked_slots: leaked, healthy: !leaked && !health.stuck.length };
}

type Answer = [number, unknown];

export function admin(method: string, path: string, q: URLSearchParams, body: any): Answer | null {
	if (!path.startsWith('/v1/admin/')) return null;
	const rest = path.slice('/v1/admin'.length);

	if (rest === '/sources' && method === 'GET') return [200, sources];
	if (rest === '/collectors' && method === 'GET') return [200, collectors()];
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
			legacy_keys: !!body.legacy_keys,
			collector: body.collector ?? null,
			secrets_set: body.api_key ? ['api_key'] : [],
			updated: new Date().toISOString()
		};
		sources.push(added);
		return [201, added];
	}
	if (rest === '/sources/commvault' && method === 'POST') {
		if (sources.some((s) => s.type === 'commvault' && s.name === body.name)) return [409, { detail: `A commvault source '${body.name}' exists` }];
		const token = !!body.access_token;
		if (token === !!(body.username || body.password)) return [422, { detail: 'Either a user name and password, or a token: not both.' }];
		if (!token && !(body.username && body.password)) return [422, { detail: 'A user name and password, or an access token.' }];
		const saas = body.deployment === 'saas';
		if (!saas && !body.base_url) return [422, { detail: 'A CommServe needs its address.' }];
		const host = String(body.base_url ?? '').replace(/^https:\/\//, '').split('/')[0];
		const added: Source = {
			type: 'commvault',
			name: body.name,
			block_id: id(1),
			collect: false,
			address: body.base_url ? `https://${host}/commandcenter/api` : 'https://api.metallic.io',
			verify_tls: body.verify_tls ?? true,
			collector: body.collector ?? null,
			deployment: saas ? 'saas' : 'software',
			login: token ? null : body.username,
			secrets_set: token ? ['access_token', ...(body.refresh_token ? ['refresh_token'] : [])] : ['password'],
			updated: new Date().toISOString()
		};
		sources.push(added);
		return [201, added];
	}
	if (rest === '/sources/rubrik' && method === 'POST') {
		if (sources.some((s) => s.type === 'rubrik' && s.name === body.name)) return [409, { detail: `A rubrik source '${body.name}' exists` }];
		const account = body.service_account ?? {};
		if (!String(account.access_token_uri ?? '').endsWith('/api/client_token'))
			return [422, { detail: [{ loc: ['body', 'service_account', 'access_token_uri'], msg: 'Value error, It should end with /api/client_token: is this an RSC service account file?' }] }];
		const added: Source = {
			type: 'rubrik',
			name: body.name,
			block_id: id(1),
			collect: false,
			address: account.access_token_uri,
			verify_tls: true,
			collector: body.collector ?? null,
			login: account.name ?? null,
			secrets_set: ['client_secret'],
			updated: new Date().toISOString()
		};
		sources.push(added);
		return [201, added];
	}
	const source = rest.match(/^\/sources\/(commvault|rubrik|netbackup)\/([^/]+)(\/check)?$/);
	if (source) {
		const found = sources.find((s) => s.type === source[1] && s.name === source[2]);
		if (!found) return [404, { detail: `No ${source[1]} source '${source[2]}'` }];
		if (source[3]) return [201, startSourceCheck(found)];
		if (method === 'PATCH') {
			if (body.collect !== undefined && body.collect !== null) found.collect = body.collect;
			if (body.collector !== undefined && body.collector !== null) found.collector = body.collector || null;
			if (body.legacy_keys !== undefined && body.legacy_keys !== null) found.legacy_keys = body.legacy_keys;
			if (body.verify_tls !== undefined && body.verify_tls !== null) found.verify_tls = body.verify_tls;
			if (body.access_token) {
				found.secrets_set = ['access_token', ...(body.refresh_token ? ['refresh_token'] : [])];
				found.login = null;
			} else if (body.password) {
				found.secrets_set = ['password'];
				if (body.username) found.login = body.username;
			}
			if (body.service_account) found.login = body.service_account.name ?? null;
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
		// A NetBackup 10.5 primary that serves its API only on 443.
		const only443 = url.includes('nbu443');
		const answered = only443 ? url.replace(':1556/', ':443/') : url;
		const moved: [string, string][] = only443
			? [
					['WARNING', `${url} did not answer (timed out); ${answered} did.`],
					['INFO', `Answered at ${answered} - use this address.`]
				]
			: [];
		return [
			201,
			addRun(flow('blt-netbackup-fetch-ca'), { url }, 'fetch-ca', [
				...moved,
				['INFO', `${answered} gave 1 CA certificate(s)`],
				['INFO', `CA certificate 1: CN=nbatd,OU=root@nbu01,O=vx - SHA-256 ${FINGERPRINT}`],
				['INFO', PEM],
				url.includes('nbu2017')
					? [
							'WARNING',
							"CA certificate 1 verifies this primary's TLS certificate only with legacy key sizes accepted: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate key too weak"
						]
					: ['INFO', "CA certificate 1 verifies this primary's TLS certificate."]
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

	if (rest === '/runs/health' && method === 'GET') return [200, healthOut()];
	if (rest === '/runs/clear-stuck' && method === 'POST') {
		const asked: string[] = body.run_ids ?? [];
		const cancelled = health.stuck.filter((r) => asked.includes(r.id)).map((r) => r.id);
		health.stuck = health.stuck.filter((r) => !cancelled.includes(r.id));
		const before = health.slots.active;
		health.slots.active = 0;
		return [200, { cancelled, skipped: asked.length - cancelled.length, slots_before: before, slots_after: 0 }];
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
