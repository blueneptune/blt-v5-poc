/** Storage monitoring (docs/storage.md): how full each target is, and
 * how soon it will be full, as the Storage pages show it. */
import type { components } from '$lib/api/schema';

export type Target = components['schemas']['StorageOut'];
export type Sample = components['schemas']['SampleOut'];

/** How worried to be. `critical`: 95 % used, or full within 14 days at
 * its rate; `warning`: 85 %, or within 60 days. */
export type Health = 'ok' | 'warning' | 'critical' | 'unknown';

export const CRITICAL_USED = 0.95;
export const WARNING_USED = 0.85;
export const CRITICAL_DAYS = 14;
export const WARNING_DAYS = 60;

export function usedFraction(target: Target): number | null {
	const used = target.latest?.used_bytes;
	const capacity = target.latest?.capacity_bytes;
	if (used == null || !capacity) return null;
	return Math.min(1, Math.max(0, used / capacity));
}

export function health(target: Target): Health {
	const used = usedFraction(target);
	const days = target.trend?.days_to_full ?? null;
	if (used === null) return 'unknown';
	if (used >= CRITICAL_USED || (days !== null && days <= CRITICAL_DAYS)) return 'critical';
	if (used >= WARNING_USED || (days !== null && days <= WARNING_DAYS)) return 'warning';
	return 'ok';
}

/** The status palette: reserved, and always shown with an icon and a word. */
export function healthColour(h: Health): string {
	switch (h) {
		case 'critical':
			return '#d03b3b';
		case 'warning':
			return '#fab219';
		default:
			return 'var(--meter-fill)';
	}
}

export const HEALTH_LABEL: Record<Health, string> = {
	ok: 'OK',
	warning: 'Filling',
	critical: 'Nearly full',
	unknown: 'No capacity'
};

export function fullIn(target: Target): string {
	const days = target.trend?.days_to_full;
	if (days == null) return target.trend ? 'not filling' : '';
	if (days < 1) return 'within a day';
	if (days < 60) return `${Math.round(days)} days`;
	if (days < 730) return `${Math.round(days / 30)} months`;
	return 'years';
}

export const GROUPS = ['source', 'host', 'kind'] as const;
export type Group = (typeof GROUPS)[number];
export const GROUP_LABEL: Record<Group, string> = {
	source: 'Source',
	host: 'MediaAgent / media server',
	kind: 'Kind'
};

/** Targets grouped for the list. By host, a target that several hosts
 * write to is listed under each - and counted once in the totals. */
export function grouped(targets: Target[], by: Group): { name: string; targets: Target[] }[] {
	const groups = new Map<string, Target[]>();
	const add = (name: string, t: Target) => groups.set(name, [...(groups.get(name) ?? []), t]);
	for (const t of targets) {
		if (by === 'source') add(`${t.source_name} (${t.source_type})`, t);
		else if (by === 'kind') add(t.kind, t);
		else if (t.hosts.length === 0) add('(no host)', t);
		else for (const host of t.hosts) add(host, t);
	}
	return [...groups.entries()]
		.sort(([a], [b]) => a.localeCompare(b))
		.map(([name, list]) => ({ name, targets: list }));
}

export function totals(targets: Target[]) {
	let capacity = 0;
	let used = 0;
	let free = 0;
	for (const t of targets) {
		capacity += t.latest?.capacity_bytes ?? 0;
		used += t.latest?.used_bytes ?? 0;
		free += t.latest?.free_bytes ?? 0;
	}
	const attention = targets.filter((t) => health(t) === 'critical' || health(t) === 'warning').length;
	return { capacity, used, free, attention };
}

/** Each sample as a point, for a chart: x in ms, y in bytes. */
export function points(samples: Sample[], key: 'used_bytes' | 'capacity_bytes') {
	return samples
		.filter((s) => s[key] != null)
		.map((s) => ({ x: Date.parse(s.collected_at), y: s[key] as number }));
}

export const RANGES = { '7d': 7, '30d': 30, '90d': 90, '1y': 365 } as const;
export type Range = keyof typeof RANGES;

/** Hourly samples for a month or less; one a day beyond. */
export function every(range: Range): 'sample' | 'day' {
	return RANGES[range] <= 30 ? 'sample' : 'day';
}
