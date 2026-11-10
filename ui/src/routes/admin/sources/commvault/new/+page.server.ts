import { redirect } from '@sveltejs/kit';
import { refused, requireAdmin } from '$lib/server/admin';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals }) => {
	const collectors = await locals.api.GET('/v1/admin/collectors');
	return { collectors: collectors.data ?? [] };
};

function text(form: FormData, name: string): string {
	return String(form.get(name) ?? '').trim();
}

export const actions: Actions = {
	/** Saves the block (collection off), starts a check, and goes to the
	 * CommCell's page to watch it. */
	add: async ({ locals, request }) => {
		requireAdmin(locals.user);
		const form = await request.formData();
		const name = text(form, 'name');
		const deployment = form.get('deployment') === 'saas' ? 'saas' : 'software';
		const byToken = form.get('sign_in') === 'token';
		const added = await locals.api.POST('/v1/admin/sources/commvault', {
			body: {
				name,
				deployment,
				base_url: text(form, 'base_url') || null,
				username: byToken ? null : text(form, 'username') || null,
				password: byToken ? null : String(form.get('password') ?? '') || null,
				access_token: byToken ? text(form, 'access_token') || null : null,
				refresh_token: byToken ? text(form, 'refresh_token') || null : null,
				account_id: deployment === 'saas' ? text(form, 'account_id') || null : null,
				tenant_guid: deployment === 'saas' ? text(form, 'tenant_guid') || null : null,
				verify_tls: form.get('verify_tls') === 'on',
				collect: false,
				collector: text(form, 'collector') || null
			}
		});
		if (!added.data) return refused(added);
		const check = await locals.api.POST('/v1/admin/sources/{source_type}/{source_name}/check', {
			params: { path: { source_type: 'commvault', source_name: name } }
		});
		redirect(303, `/admin/sources/commvault/${name}${check.data ? `?run=${check.data.id}` : ''}`);
	}
};
