import { describe, expect, it } from 'vitest';
import { modeOf, timingFromForm } from './timing';
import { until } from './format';

const form = (fields: Record<string, string>) => {
	const data = new FormData();
	for (const [key, value] of Object.entries(fields)) data.set(key, value);
	return data;
};

describe('collection timing', () => {
	it('reads the form as the API takes it', () => {
		expect(timingFromForm(form({ mode: 'default' }))).toEqual({ timezone: 'UTC' });
		expect(timingFromForm(form({ mode: 'every', every_hours: '4' }))).toEqual({ timezone: 'UTC', every_minutes: 240 });
		expect(
			timingFromForm(
				form({
					mode: 'daily',
					daily_at: '06:15',
					timezone: 'America/Chicago',
					quiet: 'on',
					quiet_from: '22:00',
					quiet_until: '04:00'
				})
			)
		).toEqual({ timezone: 'America/Chicago', daily_at: '06:15', quiet_from: '22:00', quiet_until: '04:00' });
	});

	it('says what is missing', () => {
		expect(timingFromForm(form({ mode: 'every', every_hours: '0' }))).toHaveProperty('problem');
		expect(timingFromForm(form({ mode: 'daily' }))).toHaveProperty('problem');
		expect(timingFromForm(form({ mode: 'default', quiet: 'on', quiet_from: '22:00' }))).toHaveProperty('problem');
	});

	it('knows the mode a timing is in', () => {
		expect(modeOf(null)).toBe('default');
		expect(modeOf({ every_minutes: 240 })).toBe('every');
		expect(modeOf({ daily_at: '06:15' })).toBe('daily');
	});

	it('says how far off a start is', () => {
		const now = new Date('2026-11-10T12:00:00Z');
		expect(until('2026-11-10T12:12:00Z', now)).toBe('in 12 min');
		expect(until('2026-11-10T15:00:00Z', now)).toBe('in 3 h');
		expect(until('2026-11-13T12:00:00Z', now)).toBe('in 3 days');
	});
});
