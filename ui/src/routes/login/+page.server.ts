import { fail, redirect } from '@sveltejs/kit';
import { signIn, signInConfig } from '$lib/server/auth';
import type { Actions, PageServerLoad } from './$types';

/** Where to go after signing in: a path on this site, never elsewhere. */
function nextPath(url: URL): string {
	const next = url.searchParams.get('next') ?? '/';
	return next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/\\') ? next : '/';
}

export const load: PageServerLoad = ({ locals, url }) => {
	if (locals.user) redirect(303, nextPath(url));
	const config = signInConfig();
	return { problem: config.mode === 'unconfigured' ? config.problem : null };
};

export const actions: Actions = {
	default: async ({ request, cookies, url, getClientAddress }) => {
		const form = await request.formData();
		const name = String(form.get('name') ?? '').trim();
		const password = String(form.get('password') ?? '');
		const result = await signIn(cookies, url, getClientAddress(), name, password);
		if (!result.ok) return fail(400, { name, message: result.message });
		redirect(303, nextPath(url));
	}
};
