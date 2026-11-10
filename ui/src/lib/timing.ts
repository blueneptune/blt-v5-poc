/**
 * A source's collection timing (blt.timing), as the Collection timing
 * form sends it: the product's schedule, every so many hours, or daily at
 * a time - and an optional quiet window - in the source's time zone.
 */
import type { components } from '$lib/api/schema';

export type SourceTiming = Partial<components['schemas']['SourceTiming']>;
export type TimingMode = 'default' | 'every' | 'daily';

export function modeOf(timing: SourceTiming | null | undefined): TimingMode {
	if (timing?.daily_at) return 'daily';
	if (timing?.every_minutes) return 'every';
	return 'default';
}

/** Time zones offered first; any IANA name may be typed. */
export const COMMON_ZONES = [
	'UTC',
	'America/Chicago',
	'America/New_York',
	'America/Denver',
	'America/Los_Angeles',
	'Europe/London',
	'Europe/Amsterdam',
	'Asia/Kolkata',
	'Asia/Singapore',
	'Australia/Sydney'
];

/** The form's fields as the API's `timing`, or what is wrong with them. */
export function timingFromForm(
	form: FormData
): components['schemas']['SourceTiming'] | { problem: string } {
	const text = (key: string) => String(form.get(key) ?? '').trim();
	const mode = text('mode') as TimingMode;
	const timing: components['schemas']['SourceTiming'] = { timezone: text('timezone') || 'UTC' };
	if (mode === 'every') {
		const hours = Number(text('every_hours'));
		if (!Number.isFinite(hours) || hours < 0.25 || hours > 168)
			return { problem: 'Every how many hours? From 0.25 to 168.' };
		timing.every_minutes = Math.round(hours * 60);
	} else if (mode === 'daily') {
		if (!text('daily_at')) return { problem: 'Daily at what time?' };
		timing.daily_at = text('daily_at');
	}
	if (form.get('quiet') === 'on') {
		if (!text('quiet_from') || !text('quiet_until'))
			return { problem: 'A quiet window needs both its start and its end.' };
		timing.quiet_from = text('quiet_from');
		timing.quiet_until = text('quiet_until');
	}
	return timing;
}
