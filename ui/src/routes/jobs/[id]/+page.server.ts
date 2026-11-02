import { error } from '@sveltejs/kit';
import { must } from '$lib/server/api';
import type { PageServerLoad } from './$types';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const load: PageServerLoad = async ({ locals, params }) => {
	if (!UUID.test(params.id)) error(404, 'No such job');
	const job = must(await locals.api.GET('/v1/jobs/{job_id}', { params: { path: { job_id: params.id } } }));
	return { job };
};
