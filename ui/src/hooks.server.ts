import { error, redirect, type Handle } from '@sveltejs/kit';
import { apiFor } from '$lib/server/api';
import { currentUser, signInConfig } from '$lib/server/auth';

/** Served to anyone: the probe, and the page to sign in on. */
const PUBLIC = ['/healthz', '/login'];

export const handle: Handle = async ({ event, resolve }) => {
	const config = signInConfig();
	const user = currentUser(event, config);
	event.locals.user = user;
	event.locals.api = apiFor(user, event.fetch);

	const path = event.url.pathname;
	if (!PUBLIC.some((p) => path === p || path.startsWith(`${p}/`)) && !user) {
		if (config.mode === 'unconfigured') error(503, `Sign-in is not set up: ${config.problem}`);
		const next = path === '/' ? '' : `?next=${encodeURIComponent(path + event.url.search)}`;
		redirect(303, `/login${next}`);
	}
	return resolve(event);
};
