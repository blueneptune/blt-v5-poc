import { must } from '$lib/server/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals }) => {
	const week = new Date(Date.now() - 7 * 24 * 3_600_000).toISOString();
	const [sources, summary] = await Promise.all([
		locals.api.GET('/v1/sources'),
		locals.api.GET('/v1/jobs/summary', { params: { query: { group: ['source'], since: week } } })
	]);
	const byName = new Map(must(summary).map((row) => [`${row.source_type}/${row.source_name}`, row]));
	return {
		sources: must(sources).map((source) => ({
			...source,
			week: byName.get(`${source.type}/${source.name}`) ?? null
		}))
	};
};
