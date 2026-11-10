import { fail, redirect } from '@sveltejs/kit';
import { parseServiceAccount } from '$lib/admin';
import { refused, requireAdmin } from '$lib/server/admin';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals }) => {
	const collectors = await locals.api.GET('/v1/admin/collectors');
	return { collectors: collectors.data ?? [] };
};

/** The largest file taken: a service account file is a few hundred bytes. */
const MAX_FILE = 64 * 1024;

export const actions: Actions = {
	/** Reads the service account file, saves the block (collection off),
	 * starts a check, and goes to the account's page to watch it. The
	 * file's contents go to the API and nowhere else. */
	add: async ({ locals, request }) => {
		requireAdmin(locals.user);
		const form = await request.formData();
		const name = String(form.get('name') ?? '').trim();
		const file = form.get('service_account');
		if (!(file instanceof File) || file.size === 0)
			return fail(400, { message: 'Choose the service account’s JSON file.' });
		if (file.size > MAX_FILE) return fail(400, { message: 'That file is too big to be a service account file.' });
		const account = parseServiceAccount(await file.text());
		if ('problem' in account) return fail(400, { message: account.problem });
		const added = await locals.api.POST('/v1/admin/sources/rubrik', {
			body: {
				name,
				service_account: account,
				verify_tls: true,
				collect: false,
				collector: String(form.get('collector') ?? '').trim() || null
			}
		});
		if (!added.data) return refused(added);
		const check = await locals.api.POST('/v1/admin/sources/{source_type}/{source_name}/check', {
			params: { path: { source_type: 'rubrik', source_name: name } }
		});
		redirect(303, `/admin/sources/rubrik/${name}${check.data ? `?run=${check.data.id}` : ''}`);
	}
};
