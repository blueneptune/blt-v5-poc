import { fail, redirect } from '@sveltejs/kit';
import { parameterFields, parameterValues } from '$lib/admin';
import { reason, refused, requireAdmin } from '$lib/server/admin';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals }) => {
	const result = await locals.api.GET('/v1/admin/deployments');
	return result.data
		? { deployments: result.data, problem: null }
		: { deployments: [], problem: reason(result.error, result.response) };
};

const id = (form: FormData) => String(form.get('id') ?? '');

export const actions: Actions = {
	pause: async ({ locals, request }) => {
		requireAdmin(locals.user);
		const form = await request.formData();
		const params = { params: { path: { deployment_id: id(form) } } };
		const result =
			form.get('paused') === 'true'
				? await locals.api.POST('/v1/admin/deployments/{deployment_id}/pause', params)
				: await locals.api.POST('/v1/admin/deployments/{deployment_id}/resume', params);
		if (!result.data) return refused(result, { id: id(form) });
	},

	run: async ({ locals, request }) => {
		requireAdmin(locals.user);
		const form = await request.formData();
		const deployments = await locals.api.GET('/v1/admin/deployments');
		if (!deployments.data) return refused(deployments);
		const deployment = deployments.data.find((d) => d.id === id(form));
		if (!deployment) return fail(404, { id: id(form), message: 'That deployment is gone.' });
		let parameters: Record<string, unknown>;
		try {
			parameters = parameterValues(parameterFields(deployment.parameter_schema, deployment.parameters), form);
		} catch (e) {
			return fail(400, { id: deployment.id, message: e instanceof Error ? e.message : String(e) });
		}
		const run = await locals.api.POST('/v1/admin/deployments/{deployment_id}/runs', {
			params: { path: { deployment_id: deployment.id } },
			body: { parameters }
		});
		if (!run.data) return refused(run, { id: deployment.id });
		redirect(303, `/admin/runs/${run.data.id}`);
	}
};
