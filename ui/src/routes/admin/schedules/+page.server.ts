import { fail, redirect } from '@sveltejs/kit';
import { parameterFields, parameterValues } from '$lib/admin';
import { reason, refused, requireAdmin } from '$lib/server/admin';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals }) => {
	const [result, health] = await Promise.all([
		locals.api.GET('/v1/admin/deployments'),
		locals.api.GET('/v1/admin/runs/health')
	]);
	return {
		deployments: result.data ?? [],
		problem: result.data ? null : reason(result.error, result.response),
		// Only a panel: the page works without it.
		health: health.data ?? null
	};
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

	clearStuck: async ({ locals, request }) => {
		requireAdmin(locals.user);
		const form = await request.formData();
		const result = await locals.api.POST('/v1/admin/runs/clear-stuck', {
			body: {
				run_ids: form.getAll('run').map(String),
				stuck_after_minutes: Number(form.get('stuck_after_minutes') ?? 120)
			}
		});
		if (!result.data) return refused(result, { cleared: null });
		return { cleared: result.data };
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
