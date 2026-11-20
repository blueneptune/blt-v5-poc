import { describe, expect, it } from 'vitest';
import { tally, type AssetSummary } from './assets';

const summary: AssetSummary = {
	stale_days: 7,
	last_inventory: {},
	counts: [
		{ source_type: 'commvault', source_name: 'cs', kind: 'vm', verdict: 'unprotected', count: 3 },
		{ source_type: 'rubrik', source_name: 'acme', kind: 'vm', verdict: 'unprotected', count: 2 },
		{ source_type: 'commvault', source_name: 'cs', kind: 'database', verdict: 'stale', count: 4 }
	]
};

describe('tally', () => {
	it('adds every source together', () => {
		expect(tally(summary)).toEqual({ unprotected: 5, stale: 4, protected: 0, excluded: 0, unknown: 0 });
	});
	it('keeps to the chosen kinds', () => {
		expect(tally(summary, ['database']).stale).toBe(4);
		expect(tally(summary, ['database']).unprotected).toBe(0);
	});
});
