import { error } from '@sveltejs/kit';
import { must } from '$lib/server/api';
import { RANGES, every, type Range } from '$lib/storage';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, url }) => {
	const asked = url.searchParams.get('range');
	const range: Range = asked && asked in RANGES ? (asked as Range) : '30d';
	const [target, samples] = await Promise.all([
		locals.api.GET('/v1/storage/{storage_id}', { params: { path: { storage_id: params.id } } }),
		locals.api.GET('/v1/storage/{storage_id}/samples', {
			params: { path: { storage_id: params.id }, query: { days: RANGES[range], every: every(range) } }
		})
	]);
	if (target.response.status === 404 || target.response.status === 422) error(404, 'No such storage target');
	return { range, target: must(target), samples: must(samples) };
};
