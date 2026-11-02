import { describe, expect, it } from 'vitest';
import { DEFAULT_FILTERS, apiQuery, filtersToQuery, parseFilters } from './jobs';
import { bytes, duration } from './format';

const parse = (query: string) => parseFilters(new URLSearchParams(query));

describe('job filters in the URL', () => {
	it('defaults to backups over the last day', () => {
		expect(parse('')).toEqual(DEFAULT_FILTERS);
		expect(filtersToQuery(DEFAULT_FILTERS)).toBe('');
	});

	it('keeps "every kind" in a link, so the default does not come back', () => {
		const every = { ...DEFAULT_FILTERS, kinds: [] };
		expect(filtersToQuery(every)).toBe('?kind=all');
		expect(parse('kind=all').kinds).toEqual([]);
	});

	it('round-trips every filter', () => {
		const filters = {
			kinds: ['backup', 'restore'],
			states: ['failed', 'partial'],
			products: ['rubrik'],
			sources: ['work'],
			asset: 'web',
			protection: 'Gold',
			window: '7d' as const,
			active: true,
			page: 3
		};
		expect(parse(filtersToQuery(filters).slice(1))).toEqual(filters);
	});

	it('ignores what it does not understand', () => {
		const filters = parse('window=forever&page=-2&active=maybe');
		expect([filters.window, filters.page, filters.active]).toEqual(['24h', 1, null]);
	});

	it('turns the window into a since time for the API', () => {
		const now = new Date('2026-11-02T12:00:00Z');
		expect(apiQuery(DEFAULT_FILTERS, now)).toMatchObject({
			kind: ['backup'],
			since: '2026-11-01T12:00:00.000Z'
		});
		expect(apiQuery({ ...DEFAULT_FILTERS, window: 'all', kinds: [] }, now)).toMatchObject({
			kind: undefined,
			since: undefined
		});
	});
});

describe('formatting', () => {
	it('shows bytes in binary units', () => {
		expect([bytes(512), bytes(1536), bytes(10 * 1024 ** 3), bytes(null)]).toEqual([
			'512 B',
			'1.5 KiB',
			'10 GiB',
			''
		]);
	});

	it('shows how long a job took', () => {
		expect(duration('2026-11-02T12:00:00Z', '2026-11-02T13:05:30Z')).toBe('1h 5m');
		expect(duration(null, null)).toBe('');
	});
});
