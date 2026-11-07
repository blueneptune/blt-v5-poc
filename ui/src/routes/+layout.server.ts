import { NAV, navFor } from '$lib/nav';
import { allowed } from '$lib/server/auth';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = ({ locals }) => ({
	user: locals.user,
	nav: navFor(NAV, (role) => allowed(locals.user, role))
});
