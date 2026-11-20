import { must } from '$lib/server/api';
import { KINDS, VERDICTS, WINDOWS, type Kind, type Verdict } from '$lib/assets';
import type { PageServerLoad } from './$types';

const PAGE = 100;

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
	const page = Math.max(1, Math.floor(Number(q.get('page')) || 1));
	const [assets, summary] = await Promise.all([
		locals.api.GET('/v1/assets', {
			params: {
				query: {
					stale_days: days,
					source,
					kind: kind.length ? kind : undefined,
					verdict: verdict.length ? verdict : undefined,
					name,
					limit: PAGE,
					offset: (page - 1) * PAGE
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
		page,
		pages: Math.max(1, Math.ceil(Number(assets.response.headers.get('X-Total-Count') ?? 0) / PAGE)),
		total: Number(assets.response.headers.get('X-Total-Count') ?? 0),
		windows: WINDOWS,
		assets: must(assets),
		summary: must(summary)
	};
};
