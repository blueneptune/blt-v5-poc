import { PAGE_SIZE, apiQuery, parseFilters } from '$lib/jobs';
import { must } from '$lib/server/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, url }) => {
	const filters = parseFilters(url.searchParams);
	const query = apiQuery(filters, new Date());
	const grouped = url.searchParams.get('view') === 'protection';

	const [jobs, sources, groups] = await Promise.all([
		grouped
			? null
			: locals.api.GET('/v1/jobs', {
					params: {
						query: { ...query, limit: PAGE_SIZE, offset: (filters.page - 1) * PAGE_SIZE }
					}
				}),
		locals.api.GET('/v1/sources'),
		grouped
			? locals.api.GET('/v1/jobs/summary', {
					params: { query: { ...query, group: ['source', 'protection'] } }
				})
			: null
	]);
	return {
		filters,
		grouped,
		jobs: jobs ? must(jobs) : [],
		total: jobs ? Number(jobs.response.headers.get('X-Total-Count') ?? 0) : 0,
		groups: groups ? must(groups).sort((a, b) => b.total - a.total) : [],
		sources: must(sources)
	};
};
