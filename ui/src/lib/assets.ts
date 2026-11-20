/** The Assets page: what every backup source says exists, each with a
 * verdict (GET /v1/assets, blt.api.assets). */
import type { components } from '$lib/api/schema';

export type Asset = components['schemas']['Asset'];
export type AssetSummary = components['schemas']['AssetSummary'];
export type Verdict = Asset['verdict'];
export type Kind = Asset['kind'];

/** Worst first: the order the page lists verdicts in. */
export const VERDICTS: Verdict[] = ['unprotected', 'stale', 'protected', 'excluded', 'unknown'];
export const KINDS: Kind[] = ['host', 'vm', 'database', 'instance', 'file_system', 'm365', 'other'];
export const WINDOWS = [1, 7, 30] as const;

export const VERDICT_LABEL: Record<Verdict, string> = {
	unprotected: 'Not protected',
	stale: 'Stale',
	protected: 'Protected',
	excluded: 'Excluded',
	unknown: 'Unknown'
};

export const VERDICT_HELP: Record<Verdict, string> = {
	unprotected: 'The source knows of it, and nothing protects it',
	stale: 'Protected, but no backup within the window',
	protected: 'Protected, with a backup within the window',
	excluded: 'Deliberately left out of protection',
	unknown: 'The source does not say'
};

export const KIND_LABEL: Record<Kind, string> = {
	host: 'Hosts',
	vm: 'VMs',
	database: 'Databases',
	instance: 'Instances',
	file_system: 'File systems',
	m365: 'Microsoft 365',
	other: 'Other'
};

/** One of a kind, for a table cell. */
export const KIND_ONE: Record<Kind, string> = {
	host: 'Host',
	vm: 'VM',
	database: 'Database',
	instance: 'Instance',
	file_system: 'File system',
	m365: 'Microsoft 365',
	other: 'Other'
};

/** Badge classes: status colours, never reused for anything else. */
export function verdictClass(verdict: Verdict): string {
	switch (verdict) {
		case 'protected':
			return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300';
		case 'stale':
			return 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300';
		case 'unprotected':
			return 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300';
		default:
			return 'bg-muted text-muted-foreground';
	}
}

/** How many of each verdict, from the summary, for the chosen kinds. */
export function tally(summary: AssetSummary, kinds: Kind[] = []): Record<Verdict, number> {
	const out = Object.fromEntries(VERDICTS.map((v) => [v, 0])) as Record<Verdict, number>;
	for (const c of summary.counts) {
		if (kinds.length && !kinds.includes(c.kind)) continue;
		out[c.verdict] += c.count;
	}
	return out;
}
