/**
 * The made-up sources and jobs the fake API (fake-api.ts) answers with,
 * shared with the page tests so they can name what they expect to see.
 * Times are relative to when the file is loaded: everything happened in
 * the last 20 hours.
 */
import type { components } from '../src/lib/api/schema';

export type Job = components['schemas']['CommonJobOut'];
type Source = components['schemas']['SourceOut'];

export const API_KEY = 'page-tests';
/** The UI's sign-in, for the page tests only. */
export const USER = 'admin';
export const PASSWORD = 'page-tests-password';
/** Where the sign-in project keeps the signed-in cookie. */
export const SIGNED_IN = 'test-results/.signed-in.json';
const HOUR = 3_600_000;
const now = Date.now();

const uuid = (n: number, kind = 0) =>
	`00000000-0000-4000-8${kind}00-${n.toString(16).padStart(12, '0')}`;

export const SOURCES: Source[] = [
	{ id: uuid(1, 1), type: 'commvault', name: 'cv-toaster', created_at: new Date(now - 30 * 24 * HOUR).toISOString() },
	{ id: uuid(2, 1), type: 'rubrik', name: 'rsc-work', created_at: new Date(now - 30 * 24 * HOUR).toISOString() }
];

// 130 jobs over the last 20 hours: mostly backups that succeeded, some
// failures with a message, a few restores and copies, two still running.
const STATES = ['succeeded', 'succeeded', 'succeeded', 'succeeded', 'failed', 'partial', 'succeeded', 'warning'];
const KINDS = ['backup', 'backup', 'backup', 'backup', 'backup', 'restore', 'backup', 'copy', 'backup', 'maintenance'];

export const JOBS: Job[] = Array.from({ length: 130 }, (_, i) => {
	const source = SOURCES[i % 3 === 2 ? 1 : 0];
	const running = i < 2;
	const state = running ? 'running' : STATES[i % STATES.length];
	const started = now - (i * 9 + 5) * 60_000;
	return {
		id: uuid(i + 1),
		source_id: source.id,
		source_type: source.type,
		source_name: source.name,
		product_job_key: String(1000 + i),
		product_path: source.type === 'commvault' ? `/jobs/${1000 + i}` : `/activity-series/${1000 + i}`,
		kind: KINDS[i % KINDS.length],
		state,
		product_status: running ? 'Running' : state === 'succeeded' ? 'Completed' : state,
		is_active: running,
		started_at: new Date(started).toISOString(),
		ended_at: running ? null : new Date(started + (i % 7 + 1) * 60_000).toISOString(),
		updated_at: new Date(started + 60_000).toISOString(),
		asset_name: `lab-host-${String(i % 17).padStart(2, '0')}`,
		asset_type: 'host',
		protection: i % 11 === 0 ? null : i % 2 ? 'Gold' : 'Standard Plan',
		bytes_transferred: (i % 9) * 1024 ** 2,
		bytes_protected: (i % 9) * 4 * 1024 ** 2,
		message: state === 'failed' ? `Failed to back up lab-host-${i % 17}: the client did not answer` : null,
		parent_id: null,
		source_object_id: null,
		attempt: 1,
		first_seen_at: new Date(started).toISOString(),
		last_collected_at: new Date(now).toISOString()
	};
});

type Storage = components['schemas']['StorageOut'];
type Sample = components['schemas']['SampleOut'];
const GIB = 1024 ** 3;

// Three targets: a dedupe pool filling steadily (full in about 26 days),
// one nearly full, and a cloud pool with no capacity to speak of.
function storage(n: number, name: string, over: Partial<Storage>): Storage {
	return {
		id: uuid(n, 2),
		source_id: SOURCES[0].id,
		source_type: 'commvault',
		source_name: 'cv-toaster',
		product_key: String(n),
		name,
		kind: 'dedupe',
		product_type: 'DEDUPLICATION',
		hosts: ['ma01'],
		server: null,
		status: 'Online',
		present: true,
		first_seen_at: new Date(now - 30 * 24 * HOUR).toISOString(),
		last_seen_at: new Date(now - 10 * 60_000).toISOString(),
		attributes: {},
		latest: null,
		trend: null,
		...over
	};
}

export const SAMPLES: Sample[] = Array.from({ length: 4 * 24 }, (_, i) => {
	const used = 40 * GIB + i * (2 * GIB) / 24;
	return {
		collected_at: new Date(now - (4 * 24 - i) * HOUR).toISOString(),
		capacity_bytes: 100 * GIB,
		used_bytes: Math.round(used),
		free_bytes: Math.round(100 * GIB - used)
	};
});

export const STORAGE: Storage[] = [
	storage(1, 'pool-steady', {
		hosts: ['ma01', 'ma02'],
		latest: { ...SAMPLES[SAMPLES.length - 1], dedupe_ratio: 7.64, logical_bytes: 300 * GIB, physical_bytes: 40 * GIB },
		trend: { days: 4, used_bytes_per_day: 2 * GIB, days_to_full: 26 },
		attributes: { mount_paths: [{ name: 'mount-01', status: 'Ready' }] }
	}),
	storage(2, 'pool-full', {
		hosts: ['ma02'],
		latest: { collected_at: new Date(now - 600_000).toISOString(), capacity_bytes: 10 * GIB, used_bytes: 9.7 * GIB, free_bytes: 0.3 * GIB }
	}),
	storage(3, 's3-archive', { kind: 'cloud', product_type: 'CLOUD', hosts: ['ma01'] })
];

// Where NetBackup's data goes (GET /v1/storage/flows): two primaries
// replicating to each other, one pool nearly full.
type Flows = components['schemas']['StorageFlows'];
const TIB = 1024 * GIB;
const nbuPool = (n: number, name: string, primary: string, used: number): Storage =>
	storage(n, name, {
		source_type: 'netbackup',
		source_name: primary,
		product_type: 'MSDP',
		latest: { collected_at: new Date(now - 600_000).toISOString(), capacity_bytes: 100 * TIB, used_bytes: used * TIB, free_bytes: (100 - used) * TIB }
	});
const MSDP_A = nbuPool(11, 'msdp-a', 'nbu-a', 97);
const MSDP_B = nbuPool(12, 'msdp-b', 'nbu-b', 40);
export const FLOWS: Flows = {
	primary: null,
	primaries: ['nbu-a', 'nbu-b'],
	since: new Date(now - 7 * 24 * HOUR).toISOString(),
	until: new Date(now).toISOString(),
	nodes: [
		{ id: 'backups:nbu-a', column: 0, label: 'Backups on nbu-a', source_name: 'nbu-a', storage_id: null },
		{ id: `copies:${MSDP_B.id}`, column: 0, label: 'Copies from msdp-b', source_name: 'nbu-b', storage_id: null },
		{ id: `pool:${MSDP_A.id}`, column: 1, label: 'msdp-a', source_name: 'nbu-a', storage_id: MSDP_A.id },
		{ id: `pool:${MSDP_B.id}`, column: 1, label: 'msdp-b', source_name: 'nbu-b', storage_id: MSDP_B.id }
	],
	links: [
		{ source: 'backups:nbu-a', target: `pool:${MSDP_A.id}`, kind: 'backup', bytes: 12 * TIB, jobs: 40 },
		{ source: `copies:${MSDP_B.id}`, target: `pool:${MSDP_A.id}`, kind: 'replicate', bytes: 5 * TIB, jobs: 9 }
	],
	targets: [MSDP_A, MSDP_B, nbuPool(13, 'idle-pool', 'nbu-b', 5)],
	unmatched_replications: 2
};

// One primary (?primary=nbu-a): its copies, pool -> SLP -> pool.
const TAPE_A = nbuPool(14, 'tape-a', 'nbu-a', 20);
export const FLOWS_NBU_A: Flows = {
	...FLOWS,
	primary: 'nbu-a',
	nodes: [
		{ id: `from:${MSDP_A.id}`, column: 0, label: 'msdp-a', source_name: 'nbu-a', storage_id: MSDP_A.id },
		{ id: 'slp:SLP_Gold_DR', column: 1, label: 'SLP_Gold_DR', source_name: 'nbu-a', storage_id: null },
		{ id: 'slp:SLP_Tape_Monthly', column: 1, label: 'SLP_Tape_Monthly', source_name: 'nbu-a', storage_id: null },
		{ id: `pool:${MSDP_B.id}`, column: 2, label: 'msdp-b', source_name: 'nbu-b', storage_id: MSDP_B.id },
		{ id: `pool:${TAPE_A.id}`, column: 2, label: 'tape-a', source_name: 'nbu-a', storage_id: TAPE_A.id }
	],
	links: [
		{ source: `from:${MSDP_A.id}`, target: 'slp:SLP_Gold_DR', kind: 'replicate', bytes: 8 * TIB, jobs: 30 },
		{ source: 'slp:SLP_Gold_DR', target: `pool:${MSDP_B.id}`, kind: 'replicate', bytes: 8 * TIB, jobs: 30 },
		{ source: `from:${MSDP_A.id}`, target: 'slp:SLP_Tape_Monthly', kind: 'duplicate', bytes: 3 * TIB, jobs: 6 },
		{ source: 'slp:SLP_Tape_Monthly', target: `pool:${TAPE_A.id}`, kind: 'duplicate', bytes: 3 * TIB, jobs: 6 }
	],
	targets: [...FLOWS.targets, TAPE_A],
	unmatched_replications: 0
};
