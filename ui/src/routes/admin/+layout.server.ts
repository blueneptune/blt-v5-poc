import { requireAdmin } from '$lib/server/admin';
import type { LayoutServerLoad } from './$types';

// Every page under /admin; each action checks again, as a layout's load
// does not run for a form post.
export const load: LayoutServerLoad = ({ locals }) => {
	requireAdmin(locals.user);
};
