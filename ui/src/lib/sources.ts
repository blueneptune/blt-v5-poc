/**
 * What the sources page says about a source's collection (docs/ui.md,
 * "Overdue sources"): nothing when it is on time; otherwise a sentence
 * for the banner and a short line for its card.
 */
import { ago } from './format';

export interface Collection {
	name: string;
	type: string;
	overdue: boolean;
	jobs_overdue?: boolean;
	last_collected_at?: string | null;
	last_jobs_collected_at?: string | null;
	last_jobs_status?: string | null;
	last_inventory_status?: string | null;
}

export interface Problem {
	banner: string;
	card: string;
}

export function collectionProblem(source: Collection, hours: number, now: Date = new Date()): Problem | null {
	const who = `${source.name} (${source.type})`;
	if (source.overdue) {
		const at = source.last_collected_at;
		return {
			banner: `${who} has not been collected in over ${hours} h (${at ? `last ${ago(at, now)}` : 'never'}).`,
			card: `Overdue: ${at ? `last collected ${ago(at, now)}` : 'never collected'}`
		};
	}
	if (source.jobs_overdue) {
		const failing = source.last_jobs_status === 'failed';
		const what = failing ? 'job collection is failing' : `job collection has not succeeded in over ${hours} h`;
		const inventory = source.last_inventory_status === 'succeeded' ? ' while inventory is succeeding' : '';
		const last = source.last_jobs_collected_at
			? `jobs last collected ${ago(source.last_jobs_collected_at, now)}`
			: 'no jobs collected yet';
		return {
			banner: `Source ${who}: ${what}${inventory} (${last}). Please investigate.`,
			card: `${failing ? 'Job collection failing' : 'Job collection overdue'}: ${last}${inventory ? '; inventory OK' : ''}`
		};
	}
	return null;
}
