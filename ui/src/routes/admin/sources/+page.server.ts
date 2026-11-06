import { redirect } from '@sveltejs/kit';
import { reason, refused, requireAdmin } from '$lib/server/admin';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals }) => {
	const result = await locals.api.GET('/v1/admin/sources');
	return result.data
		? { sources: result.data, problem: null }
		: { sources: [], problem: reason(result.error, result.response) };
};

type Kind = 'commvault' | 'rubrik' | 'netbackup';

export const actions: Actions = {
	/** Turns collection on or off. */
	collect: async ({ locals, request }) => {
		requireAdmin(locals.user);
		const form = await request.formData();
		const type = String(form.get('type')) as Kind;
		const name = String(form.get('name'));
		const result = await locals.api.PATCH('/v1/admin/sources/{source_type}/{source_name}', {
			params: { path: { source_type: type, source_name: name } },
			body: { collect: form.get('collect') === 'true' }
		});
		if (!result.data) return refused(result, { name });
		redirect(303, '/admin/sources');
	}
};
