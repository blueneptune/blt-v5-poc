import { redirect } from '@sveltejs/kit';
import { signOut } from '$lib/server/auth';
import type { Actions, PageServerLoad } from './$types';

// Signing out is a form post (from the side panel), never a link a page
// could make the browser follow.
export const load: PageServerLoad = () => redirect(303, '/');

export const actions: Actions = {
	default: ({ cookies }) => {
		signOut(cookies);
		redirect(303, '/login');
	}
};
