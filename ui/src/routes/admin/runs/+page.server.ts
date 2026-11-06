import { reason } from '$lib/server/admin';
import type { PageServerLoad } from './$types';

const STATES = {
	all: undefined,
	running: ['RUNNING', 'PENDING', 'CANCELLING'],
	failed: ['FAILED', 'CRASHED'],
	completed: ['COMPLETED'],
	scheduled: ['SCHEDULED']
} as const;
export type RunFilter = keyof typeof STATES;

export const load: PageServerLoad = async ({ locals, url }) => {
	const filter = (url.searchParams.get('state') ?? 'all') as RunFilter;
	const deployment = url.searchParams.get('deployment');
	const page = Math.max(0, Number(url.searchParams.get('page') ?? 0) || 0);
	const state = STATES[filter in STATES ? filter : 'all'];
	const [runs, deployments] = await Promise.all([
		locals.api.GET('/v1/admin/runs', {
			params: {
				query: {
					state: state ? [...state] : undefined,
					deployment_id: deployment ?? undefined,
					limit: 50,
					offset: page * 50
				}
			}
		}),
		locals.api.GET('/v1/admin/deployments')
	]);
	return {
		runs: runs.data ?? [],
		problem: runs.data ? null : reason(runs.error, runs.response),
		filter,
		page,
		deployment: (deployments.data ?? []).find((d) => d.id === deployment) ?? null
	};
};
