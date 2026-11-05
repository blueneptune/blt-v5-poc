import { describe, expect, it } from 'vitest';
import { fullIn, grouped, health, totals, usedFraction, type Target } from './storage';

const GIB = 1024 ** 3;

function target(over: Partial<Target> & { used?: number; capacity?: number; days?: number | null } = {}): Target {
	const { used = 50, capacity = 100, days, ...rest } = over;
	return {
		id: crypto.randomUUID(),
		source_id: 's',
		source_type: 'commvault',
		source_name: 'cv',
		product_key: '1',
		name: 'pool',
		kind: 'dedupe',
		hosts: ['ma01'],
		present: true,
		first_seen_at: '2026-11-01T00:00:00Z',
		last_seen_at: '2026-11-05T00:00:00Z',
		attributes: {},
		latest: {
			collected_at: '2026-11-05T00:00:00Z',
			used_bytes: used * GIB,
			capacity_bytes: capacity * GIB,
			free_bytes: (capacity - used) * GIB
		},
		trend: days === undefined ? null : { days: 7, used_bytes_per_day: GIB, days_to_full: days },
		...rest
	};
}

describe('health', () => {
	it('by how full', () => {
		expect(health(target({ used: 50 }))).toBe('ok');
		expect(health(target({ used: 86 }))).toBe('warning');
		expect(health(target({ used: 96 }))).toBe('critical');
	});
	it('by how soon it is full', () => {
		expect(health(target({ used: 10, days: 200 }))).toBe('ok');
		expect(health(target({ used: 10, days: 45 }))).toBe('warning');
		expect(health(target({ used: 10, days: 10 }))).toBe('critical');
	});
	it('unknown without a capacity', () => {
		expect(health(target({ latest: null }))).toBe('unknown');
		expect(usedFraction(target({ capacity: 0 }))).toBeNull();
	});
});

it('full in, in words', () => {
	expect(fullIn(target())).toBe('');
	expect(fullIn(target({ days: null }))).toBe('not filling');
	expect(fullIn(target({ days: 0.5 }))).toBe('within a day');
	expect(fullIn(target({ days: 26.4 }))).toBe('26 days');
	expect(fullIn(target({ days: 200 }))).toBe('7 months');
});

it('by host, a shared pool is under each host but counted once', () => {
	const shared = target({ hosts: ['ma02', 'ma01'] });
	const alone = target({ hosts: [] });
	const groups = grouped([shared, alone], 'host');
	expect(groups.map((g) => [g.name, g.targets.length])).toEqual([
		['(no host)', 1],
		['ma01', 1],
		['ma02', 1]
	]);
	expect(totals([shared, alone]).capacity).toBe(200 * GIB);
});
