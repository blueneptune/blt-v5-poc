import { NAV } from '$lib/nav';
import { allowed } from '$lib/server/auth';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = ({ locals }) => ({
	user: locals.user,
	nav: NAV.filter((item) => allowed(locals.user, item.role))
});
