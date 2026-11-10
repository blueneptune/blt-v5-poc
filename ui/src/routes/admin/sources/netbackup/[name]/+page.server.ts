import { error, fail, redirect } from '@sveltejs/kit';
import { reason, refused, requireAdmin } from '$lib/server/admin';
import { saveTiming } from '$lib/server/timing';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, url }) => {
	const [result, collectors] = await Promise.all([
		locals.api.GET('/v1/admin/sources'),
		locals.api.GET('/v1/admin/collectors')
	]);
	if (!result.data) error(502, reason(result.error, result.response));
	const source = result.data.find((s) => s.type === 'netbackup' && s.name === params.name);
	if (!source) error(404, `No NetBackup primary ${params.name}`);
	return { source, collectors: collectors.data ?? [], run: url.searchParams.get('run') };
};

const path = (name: string) => ({ params: { path: { source_type: 'netbackup' as const, source_name: name } } });

export const actions: Actions = {
	timing: ({ locals, params, request }) => saveTiming(locals, request, 'netbackup', params.name),

	check: async ({ locals, params }) => {
		requireAdmin(locals.user);
		const run = await locals.api.POST('/v1/admin/sources/{source_type}/{source_name}/check', {
			params: { path: { source_type: 'netbackup', source_name: params.name } }
		});
		if (!run.data) return refused(run);
		redirect(303, `?run=${run.data.id}`);
	},

	collect: async ({ locals, params, request }) => {
		requireAdmin(locals.user);
		const collect = (await request.formData()).get('collect') === 'true';
		const result = await locals.api.PATCH('/v1/admin/sources/{source_type}/{source_name}', {
			...path(params.name),
			body: { collect }
		});
		if (!result.data) return refused(result);
		return { saved: collect ? 'Collection is on.' : 'Collection is off.' };
	},

	/** Which collector reaches it: '' is blt's own worker. */
	collector: async ({ locals, params, request }) => {
		requireAdmin(locals.user);
		const collector = String((await request.formData()).get('collector') ?? '');
		const result = await locals.api.PATCH('/v1/admin/sources/{source_type}/{source_name}', {
			...path(params.name),
			body: { collector }
		});
		if (!result.data) return refused(result);
		return { saved: 'Saved. Check it again: the check runs from its collector.' };
	},

	/** Legacy (1024-bit) TLS keys accepted, or not, for this primary. */
	legacyKeys: async ({ locals, params, request }) => {
		requireAdmin(locals.user);
		const legacy = (await request.formData()).get('legacy_keys') === 'true';
		const result = await locals.api.PATCH('/v1/admin/sources/{source_type}/{source_name}', {
			...path(params.name),
			body: { legacy_keys: legacy }
		});
		if (!result.data) return refused(result);
		return {
			saved: legacy
				? 'Legacy key sizes are accepted for it. Check it again.'
				: 'Legacy key sizes are no longer accepted for it. Check it again.'
		};
	},

	/** Changes what was filled in; empty fields stay as they are. */
	change: async ({ locals, params, request }) => {
		requireAdmin(locals.user);
		const form = await request.formData();
		const url = String(form.get('url') ?? '').trim();
		const key = String(form.get('api_key') ?? '');
		const ca = String(form.get('ca_certificate') ?? '').trim();
		const body: Record<string, unknown> = {};
		if (url) body.url = url;
		if (key) body.api_key = key;
		if (ca) body.ca_certificate = ca;
		if (form.get('remove_ca') === 'on') body.ca_certificate = '';
		if (!Object.keys(body).length) return fail(400, { message: 'Nothing was changed.' });
		const result = await locals.api.PATCH('/v1/admin/sources/{source_type}/{source_name}', {
			...path(params.name),
			body
		});
		if (!result.data) return refused(result);
		return { saved: 'Saved. Check it again to be sure.' };
	},

	remove: async ({ locals, params, request }) => {
		requireAdmin(locals.user);
		if (String((await request.formData()).get('confirm') ?? '').trim() !== params.name)
			return fail(400, { message: `Type ${params.name} to remove it.` });
		const result = await locals.api.DELETE('/v1/admin/sources/{source_type}/{source_name}', path(params.name));
		if (result.response.status !== 204) return refused(result);
		redirect(303, '/admin/sources');
	}
};
