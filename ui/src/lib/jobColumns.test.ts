import { describe, expect, it } from 'vitest';
import {
	JOB_COLUMNS,
	MAX_WIDTH,
	MIN_WIDTH,
	defaultPrefs,
	parsePrefs,
	reduction,
	resetWidth,
	setWidth,
	shownColumns,
	toggleColumn,
	widthOf
} from './jobColumns';

describe('job columns', () => {
	it('starts with the columns the page always had', () => {
		expect(defaultPrefs().shown).toEqual(['state', 'asset', 'protection', 'source', 'kind', 'started', 'took', 'written']);
	});

	it('keeps shown columns in their own order, whatever order they were picked in', () => {
		let prefs = toggleColumn(defaultPrefs(), 'collected');
		prefs = toggleColumn(prefs, 'job_id');
		const ids = shownColumns(prefs).map((c) => c.id);
		expect(ids.indexOf('job_id')).toBeLessThan(ids.indexOf('started'));
		expect(ids.at(-1)).toBe('collected');
	});

	it('never hides the last column', () => {
		const one = { shown: ['asset'], widths: {} };
		expect(toggleColumn(one, 'asset')).toEqual(one);
	});

	it('clamps widths and resets them', () => {
		const asset = JOB_COLUMNS.find((c) => c.id === 'asset')!;
		expect(widthOf(setWidth(defaultPrefs(), 'asset', 5), asset)).toBe(MIN_WIDTH);
		expect(widthOf(setWidth(defaultPrefs(), 'asset', 5000), asset)).toBe(MAX_WIDTH);
		const wide = setWidth(defaultPrefs(), 'asset', 400.4);
		expect(widthOf(wide, asset)).toBe(400);
		expect(widthOf(resetWidth(wide, 'asset'), asset)).toBe(asset.width);
	});

	it('reads back what it stored, and survives anything else', () => {
		const prefs = setWidth(toggleColumn(defaultPrefs(), 'reduction'), 'state', 90);
		expect(parsePrefs(JSON.stringify(prefs))).toEqual(prefs);
		expect(parsePrefs(null)).toEqual(defaultPrefs());
		expect(parsePrefs('not json')).toEqual(defaultPrefs());
		expect(parsePrefs(JSON.stringify({ shown: ['gone'], widths: { gone: 10, asset: 'x' } }))).toEqual(defaultPrefs());
		expect(parsePrefs(JSON.stringify({ shown: ['kind'], widths: { kind: 9999 } }))).toEqual({
			shown: ['kind'],
			widths: { kind: MAX_WIDTH }
		});
	});

	it('shows reduction as protected per byte written', () => {
		expect(reduction(1200, 100)).toBe('12×');
		expect(reduction(250, 100)).toBe('2.5×');
		expect(reduction(null, 100)).toBe('');
		expect(reduction(100, 0)).toBe('');
	});
});
