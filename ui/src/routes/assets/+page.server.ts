import { must } from '$lib/server/api';
import { KINDS, VERDICTS, WINDOWS, type Kind, type Verdict } from '$lib/assets';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, url }) => {
	const q = url.searchParams;
	const window = Number(q.get('days'));
	const days = (WINDOWS as readonly number[]).includes(window) ? window : 7;
	const kind = q.getAll('kind').filter((k): k is Kind => KINDS.includes(k as Kind));
	// The gaps first: what nothing protects, unless asked for something
	// else. `?verdict=` (empty) is every verdict.
	const asked = q.getAll('verdict').filter((v): v is Verdict => VERDICTS.includes(v as Verdict));
	const verdict = q.has('verdict') ? asked : (['unprotected'] as Verdict[]);
	const source = q.get('source') || undefined;
	const name = q.get('name') || undefined;
	const [assets, summary] = await Promise.all([
		locals.api.GET('/v1/assets', {
			params: {
				query: {
					stale_days: days,
					source,
					kind: kind.length ? kind : undefined,
					verdict: verdict.length ? verdict : undefined,
					name,
					limit: 1000
				}
			}
		}),
		locals.api.GET('/v1/assets/summary', { params: { query: { stale_days: days } } })
	]);
	return {
		days,
		kind,
		verdict,
		source,
		name,
		windows: WINDOWS,
		assets: must(assets),
		summary: must(summary)
	};
};
