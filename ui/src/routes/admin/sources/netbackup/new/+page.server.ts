import { fail, redirect } from '@sveltejs/kit';
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
	/** Starts blt-netbackup-fetch-ca for the address in the form; the
	 * page shows its log, and the certificates in it. */
	fetchCa: async ({ locals, request }) => {
		requireAdmin(locals.user);
		const form = await request.formData();
		const url = text(form, 'url');
		if (!url) return fail(400, { message: 'Give the primary’s address first.' });
		// From the collector that will reach it: blt's own may not.
		const collector = text(form, 'collector') || null;
		const result = await locals.api.POST('/v1/admin/netbackup/ca-certificate', {
			body: { url, collector }
		});
		if (!result.data) return refused(result);
		return { caRun: result.data.id };
	},

	/** Saves the block (collection off), starts a check, and goes to the
	 * primary's page to watch it. */
	add: async ({ locals, request }) => {
		requireAdmin(locals.user);
		const form = await request.formData();
		const name = text(form, 'name');
		const added = await locals.api.POST('/v1/admin/sources/netbackup', {
			body: {
				name,
				url: text(form, 'url'),
				api_key: String(form.get('api_key') ?? ''),
				ca_certificate: text(form, 'ca_certificate') || null,
				verify_tls: form.get('verify_tls') !== 'false',
				legacy_keys: form.get('legacy_keys') === 'on',
				collect: false,
				collector: text(form, 'collector') || null
			}
		});
		if (!added.data) return refused(added);
		const check = await locals.api.POST('/v1/admin/sources/netbackup/{source_name}/check', {
			params: { path: { source_name: name } }
		});
		redirect(303, `/admin/sources/netbackup/${name}${check.data ? `?run=${check.data.id}` : ''}`);
	}
};
