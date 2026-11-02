import type { Handle } from '@sveltejs/kit';
import { apiFor } from '$lib/server/api';
import { currentUser } from '$lib/server/auth';

export const handle: Handle = async ({ event, resolve }) => {
	event.locals.user = currentUser(event);
	event.locals.api = apiFor(event.locals.user, event.fetch);
	return resolve(event);
};
