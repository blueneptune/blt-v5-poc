import { redirect } from '@sveltejs/kit';
import { isAddable } from '$lib/admin';
import { requireAdmin } from '$lib/server/admin';
import type { PageServerLoad } from './$types';

/** "Add a source": with ?type= (the Sources page's drop-down, without
 * JavaScript) straight to that product's form; without, a choice. */
export const load: PageServerLoad = async ({ locals, url }) => {
	requireAdmin(locals.user);
	const type = url.searchParams.get('type');
	if (isAddable(type)) redirect(303, `/admin/sources/${type}/new`);
	return {};
};
