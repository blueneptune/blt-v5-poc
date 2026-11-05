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
