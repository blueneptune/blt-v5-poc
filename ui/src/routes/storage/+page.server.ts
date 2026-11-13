import { must } from '$lib/server/api';
import { WINDOWS } from '$lib/sankey';
import { GROUPS, type Group } from '$lib/storage';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, url }) => {
	const asked = url.searchParams.get('by');
	const by: Group = GROUPS.includes(asked as Group) ? (asked as Group) : 'source';
	const window = Number(url.searchParams.get('flow'));
	const days = (WINDOWS as readonly number[]).includes(window) ? window : 7;
	const [targets, flows] = await Promise.all([
		locals.api.GET('/v1/storage'),
		locals.api.GET('/v1/storage/flows', { params: { query: { days } } })
	]);
	return { by, days, windows: WINDOWS, targets: must(targets), flows: must(flows) };
};
