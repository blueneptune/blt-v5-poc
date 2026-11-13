import { redirect } from '@sveltejs/kit';
import { reason, refused, requireAdmin } from '$lib/server/admin';
import { WINDOWS_COLLECTOR, downloadHref } from '$lib/server/downloads';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals }) => {
	const [result, collectors] = await Promise.all([
		locals.api.GET('/v1/admin/sources'),
		locals.api.GET('/v1/admin/collectors')
	]);
	return {
		sources: result.data ?? [],
		problem: result.data ? null : reason(result.error, result.response),
		// The page works without them: then only blt's own worker is offered.
		collectors: collectors.data ?? [],
		// Only when this server has the bundle to give (BLT_UI_TLS=on).
		windowsCollector: downloadHref(WINDOWS_COLLECTOR)
	};
};

type Kind = 'commvault' | 'rubrik' | 'netbackup';

async function patch(
	locals: App.Locals,
	request: Request,
	body: (form: FormData) => { collect?: boolean; collector?: string }
) {
	requireAdmin(locals.user);
	const form = await request.formData();
	const type = String(form.get('type')) as Kind;
	const name = String(form.get('name'));
	const result = await locals.api.PATCH('/v1/admin/sources/{source_type}/{source_name}', {
		params: { path: { source_type: type, source_name: name } },
		body: body(form)
	});
	if (!result.data) return refused(result, { name });
	redirect(303, '/admin/sources');
}

export const actions: Actions = {
	/** Turns collection on or off. */
	collect: ({ locals, request }) =>
		patch(locals, request, (form) => ({ collect: form.get('collect') === 'true' })),

	/** Which collector reaches it: '' is blt's own worker. */
	collector: ({ locals, request }) =>
		patch(locals, request, (form) => ({ collector: String(form.get('collector') ?? '') }))
};
