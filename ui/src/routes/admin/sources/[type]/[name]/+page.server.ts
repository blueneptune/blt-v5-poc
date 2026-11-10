import { error, fail, redirect } from '@sveltejs/kit';
import { parseServiceAccount } from '$lib/admin';
import { reason, refused, requireAdmin } from '$lib/server/admin';
import { saveTiming } from '$lib/server/timing';
import type { Actions, PageServerLoad } from './$types';

/** A CommCell's or a Rubrik account's page; a NetBackup primary has its
 * own (netbackup/[name]), with its CA certificate and key sizes. */
type Kind = 'commvault' | 'rubrik';
const KINDS: Kind[] = ['commvault', 'rubrik'];

function kindOf(type: string): Kind {
	if (!(KINDS as string[]).includes(type)) error(404, `No kind of source ${type}`);
	return type as Kind;
}

export const load: PageServerLoad = async ({ locals, params, url }) => {
	const type = kindOf(params.type);
	const [result, collectors] = await Promise.all([
		locals.api.GET('/v1/admin/sources'),
		locals.api.GET('/v1/admin/collectors')
	]);
	if (!result.data) error(502, reason(result.error, result.response));
	const source = result.data.find((s) => s.type === type && s.name === params.name);
	if (!source) error(404, `No ${type} source ${params.name}`);
	return { source, collectors: collectors.data ?? [], run: url.searchParams.get('run') };
};

const path = (type: Kind, name: string) => ({ params: { path: { source_type: type, source_name: name } } });

type Patch = {
	collect?: boolean;
	collector?: string;
	verify_tls?: boolean;
	base_url?: string;
	username?: string;
	password?: string;
	access_token?: string;
	refresh_token?: string;
	service_account?: { client_id: string; client_secret: string; access_token_uri: string; name: string | null };
};

async function patch(locals: App.Locals, type: Kind, name: string, body: Patch, saved: string) {
	const result = await locals.api.PATCH('/v1/admin/sources/{source_type}/{source_name}', {
		...path(type, name),
		body
	});
	if (!result.data) return refused(result);
	return { saved };
}

export const actions: Actions = {
	timing: ({ locals, params, request }) => saveTiming(locals, request, kindOf(params.type), params.name),

	check: async ({ locals, params }) => {
		requireAdmin(locals.user);
		const run = await locals.api.POST('/v1/admin/sources/{source_type}/{source_name}/check', path(kindOf(params.type), params.name));
		if (!run.data) return refused(run);
		redirect(303, `?run=${run.data.id}`);
	},

	collect: async ({ locals, params, request }) => {
		requireAdmin(locals.user);
		const collect = (await request.formData()).get('collect') === 'true';
		return patch(locals, kindOf(params.type), params.name, { collect }, collect ? 'Collection is on.' : 'Collection is off.');
	},

	/** Which collector reaches it: '' is blt's own worker. */
	collector: async ({ locals, params, request }) => {
		requireAdmin(locals.user);
		const collector = String((await request.formData()).get('collector') ?? '');
		return patch(locals, kindOf(params.type), params.name, { collector }, 'Saved. Check it again: the check runs from its collector.');
	},

	verifyTls: async ({ locals, params, request }) => {
		requireAdmin(locals.user);
		const verify = (await request.formData()).get('verify_tls') === 'true';
		return patch(
			locals,
			kindOf(params.type),
			params.name,
			{ verify_tls: verify },
			verify ? 'Its TLS certificate is checked. Check it again.' : 'Its TLS certificate is no longer checked.'
		);
	},

	/** A CommCell: a new address, or a new way to sign in (which replaces
	 * the old one whole). Empty fields stay as they are. */
	changeCommvault: async ({ locals, params, request }) => {
		requireAdmin(locals.user);
		const form = await request.formData();
		const value = (key: string) => String(form.get(key) ?? '').trim();
		const body: Patch = {};
		if (value('base_url')) body.base_url = value('base_url');
		if (value('username')) body.username = value('username');
		if (String(form.get('password') ?? '')) body.password = String(form.get('password'));
		if (value('access_token')) body.access_token = value('access_token');
		if (value('refresh_token')) body.refresh_token = value('refresh_token');
		if (!Object.keys(body).length) return fail(400, { message: 'Nothing was changed.' });
		return patch(locals, 'commvault', params.name, body, 'Saved. Check it again to be sure.');
	},

	/** A Rubrik account: a new service account file. */
	changeRubrik: async ({ locals, params, request }) => {
		requireAdmin(locals.user);
		const file = (await request.formData()).get('service_account');
		if (!(file instanceof File) || file.size === 0) return fail(400, { message: 'Choose the new service account’s file.' });
		if (file.size > 64 * 1024) return fail(400, { message: 'That file is too big to be a service account file.' });
		const account = parseServiceAccount(await file.text());
		if ('problem' in account) return fail(400, { message: account.problem });
		return patch(locals, 'rubrik', params.name, { service_account: account }, 'Saved. Check it again to be sure.');
	},

	remove: async ({ locals, params, request }) => {
		requireAdmin(locals.user);
		const type = kindOf(params.type);
		if (String((await request.formData()).get('confirm') ?? '').trim() !== params.name)
			return fail(400, { message: `Type ${params.name} to remove it.` });
		const result = await locals.api.DELETE('/v1/admin/sources/{source_type}/{source_name}', path(type, params.name));
		if (result.response.status !== 204) return refused(result);
		redirect(303, '/admin/sources');
	}
};
