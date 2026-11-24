import { describe, expect, it } from 'vitest';
import { collectionProblem } from './sources';

const now = new Date('2026-11-23T12:00:00Z');
const hoursAgo = (h: number) => new Date(now.getTime() - h * 3_600_000).toISOString();
const base = { name: 'bob', type: 'netbackup', overdue: false, jobs_overdue: false };

describe('collection problems', () => {
	it('say nothing when collection is on time', () => {
		expect(collectionProblem({ ...base, last_collected_at: hoursAgo(1) }, 12, now)).toBeNull();
	});

	it('call a source not collected at all overdue', () => {
		const problem = collectionProblem({ ...base, overdue: true, jobs_overdue: true, last_collected_at: hoursAgo(26) }, 12, now);
		expect(problem).toEqual({
			banner: 'bob (netbackup) has not been collected in over 12 h (last 26 h ago).',
			card: 'Overdue: last collected 26 h ago'
		});
	});

	it('tell job collection failing apart from inventory succeeding', () => {
		const source = {
			...base,
			jobs_overdue: true,
			last_jobs_collected_at: hoursAgo(30),
			last_jobs_status: 'failed',
			last_inventory_status: 'succeeded'
		};
		expect(collectionProblem(source, 12, now)).toEqual({
			banner:
				'Source bob (netbackup): job collection is failing while inventory is succeeding (jobs last collected 30 h ago). Please investigate.',
			card: 'Job collection failing: jobs last collected 30 h ago; inventory OK'
		});
	});

	it('do not say failing when the job runs did not fail', () => {
		const source = { ...base, jobs_overdue: true, last_jobs_status: 'running', last_inventory_status: 'succeeded' };
		expect(collectionProblem(source, 12, now)?.banner).toBe(
			'Source bob (netbackup): job collection has not succeeded in over 12 h while inventory is succeeding (no jobs collected yet). Please investigate.'
		);
	});
});
