import { env } from '$env/dynamic/private';
import { must } from '$lib/server/api';
import type { PageServerLoad } from './$types';

/**
 * A source with no succeeded collection run in this many hours is
 * overdue: BLT_UI_COLLECTION_OVERDUE_HOURS, 12 when unset or not a
 * positive number, and at most the API's 90 days.
 */
function overdueHours(value: string | undefined = env.BLT_UI_COLLECTION_OVERDUE_HOURS): number {
	const hours = Number(value);
	return value && Number.isFinite(hours) && hours > 0 ? Math.min(hours, 24 * 90) : 12;
}

export const load: PageServerLoad = async ({ locals }) => {
	const week = new Date(Date.now() - 7 * 24 * 3_600_000).toISOString();
	const hours = overdueHours();
	const [sources, summary] = await Promise.all([
		locals.api.GET('/v1/sources', { params: { query: { overdue_hours: hours } } }),
		locals.api.GET('/v1/jobs/summary', { params: { query: { group: ['source'], since: week } } })
	]);
	const byName = new Map(must(summary).map((row) => [`${row.source_type}/${row.source_name}`, row]));
	return {
		overdueHours: hours,
		sources: must(sources).map((source) => ({
			...source,
			week: byName.get(`${source.type}/${source.name}`) ?? null
		}))
	};
};
