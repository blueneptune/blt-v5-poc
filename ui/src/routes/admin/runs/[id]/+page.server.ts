import { error, redirect } from '@sveltejs/kit';
import { reason, refused, requireAdmin } from '$lib/server/admin';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params }) => {
	const result = await locals.api.GET('/v1/admin/runs/{run_id}', { params: { path: { run_id: params.id } } });
	if (!result.data) error(result.response.status === 404 ? 404 : 502, reason(result.error, result.response));
	return { run: result.data };
};

export const actions: Actions = {
	cancel: async ({ locals, params }) => {
		requireAdmin(locals.user);
		const result = await locals.api.POST('/v1/admin/runs/{run_id}/cancel', { params: { path: { run_id: params.id } } });
		if (!result.data) return refused(result);
		redirect(303, `/admin/runs/${params.id}`);
	}
};
