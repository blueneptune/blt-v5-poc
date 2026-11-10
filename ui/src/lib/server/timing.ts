/** The ?/timing action every source page shares. */
import { fail } from '@sveltejs/kit';
import { refused, requireAdmin } from '$lib/server/admin';
import { timingFromForm } from '$lib/timing';

type Kind = 'commvault' | 'rubrik' | 'netbackup';

export async function saveTiming(locals: App.Locals, request: Request, type: Kind, name: string) {
	requireAdmin(locals.user);
	const timing = timingFromForm(await request.formData());
	if ('problem' in timing) return fail(400, { message: timing.problem });
	const result = await locals.api.PATCH('/v1/admin/sources/{source_type}/{source_name}', {
		params: { path: { source_type: type, source_name: name } },
		body: { timing }
	});
	if (!result.data) return refused(result);
	return { saved: `Timing saved: ${result.data.timing_text}.` };
}
