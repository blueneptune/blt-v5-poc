import { must } from '$lib/server/api';
import { GROUPS, type Group } from '$lib/storage';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, url }) => {
	const asked = url.searchParams.get('by');
	const by: Group = GROUPS.includes(asked as Group) ? (asked as Group) : 'source';
	const targets = must(await locals.api.GET('/v1/storage'));
	return { by, targets };
};
