import { error, json } from '@sveltejs/kit';
import { requireAdmin, reason } from '$lib/server/admin';
import type { RequestHandler } from './$types';

/** A run and its log lines from `offset` on, for RunLog to poll. */
export const GET: RequestHandler = async ({ locals, params, url }) => {
	requireAdmin(locals.user);
	const offset = Number(url.searchParams.get('offset') ?? 0) || 0;
	const result = await locals.api.GET('/v1/admin/runs/{run_id}', {
		params: { path: { run_id: params.id }, query: { log_offset: offset } }
	});
	if (!result.data) error(result.response.status === 404 ? 404 : 502, reason(result.error, result.response));
	return json(result.data);
};
