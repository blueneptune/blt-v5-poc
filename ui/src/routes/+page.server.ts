import { must } from '$lib/server/api';
import type { PageServerLoad } from './$types';

const HOUR = 3_600_000;

export const load: PageServerLoad = async ({ locals }) => {
	const now = Date.now();
	const day = new Date(now - 24 * HOUR).toISOString();
	const fortnight = new Date(now - 14 * 24 * HOUR).toISOString();
	const api = locals.api;

	const [byDay, bySource, failures, active] = await Promise.all([
		api.GET('/v1/jobs/summary', { params: { query: { group: ['day'], since: fortnight } } }),
		api.GET('/v1/jobs/summary', { params: { query: { group: ['source'], since: day } } }),
		api.GET('/v1/jobs', {
			params: { query: { state: ['failed', 'partial', 'missing'], since: day, limit: 10 } }
		}),
		api.GET('/v1/jobs', { params: { query: { active: true, limit: 1 } } })
	]);
	return {
		byDay: must(byDay),
		bySource: must(bySource),
		failures: must(failures),
		failureCount: Number(failures.response.headers.get('X-Total-Count') ?? 0),
		activeCount: Number(active.response.headers.get('X-Total-Count') ?? 0)
	};
};
