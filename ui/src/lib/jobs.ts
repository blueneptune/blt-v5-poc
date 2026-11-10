/**
 * The jobs page's filters, kept in the URL so a view can be linked to and
 * the back button works - and the words and colours for blt's states and
 * kinds (docs/common-jobs.md).
 */
import type { components } from '$lib/api/schema';

export type Job = components['schemas']['CommonJobOut'];
export type JobDetail = components['schemas']['CommonJobDetail'];
export type SummaryRow = components['schemas']['SummaryRow'];

export const KINDS = ['backup', 'restore', 'copy', 'maintenance', 'scan', 'other'] as const;
export const STATES = [
	'queued',
	'running',
	'waiting',
	'succeeded',
	'warning',
	'info',
	'partial',
	'failed',
	'cancelled',
	'skipped',
	'missing',
	'unknown'
] as const;
export const PRODUCTS = ['commvault', 'rubrik', 'netbackup'] as const;

/** How far back the list looks: the filter bar's choices. */
export const WINDOWS = { '24h': 24, '7d': 24 * 7, '30d': 24 * 30, all: 0 } as const;
export type Window = keyof typeof WINDOWS;

export interface JobFilters {
	/** Empty means every kind. */
	kinds: string[];
	states: string[];
	products: string[];
	sources: string[];
	asset: string;
	protection: string;
	window: Window;
	active: boolean | null;
	page: number;
}

/** The default view, decision 3: backups, the last day. */
export const DEFAULT_FILTERS: JobFilters = {
	kinds: ['backup'],
	states: [],
	products: [],
	sources: [],
	asset: '',
	protection: '',
	window: '24h',
	active: null,
	page: 1
};

export const PAGE_SIZE = 50;

/** The filters a URL asks for. A kind of `all` means every kind, so the
 * default (backups) can be turned off and stay off in a link. */
export function parseFilters(params: URLSearchParams): JobFilters {
	const kinds = params.getAll('kind');
	const window = params.get('window');
	const active = params.get('active');
	const page = Number(params.get('page'));
	return {
		kinds: kinds.length === 0 ? DEFAULT_FILTERS.kinds : kinds.includes('all') ? [] : kinds,
		states: params.getAll('state'),
		products: params.getAll('product'),
		sources: params.getAll('source'),
		asset: params.get('asset') ?? '',
		protection: params.get('protection') ?? '',
		window: window && window in WINDOWS ? (window as Window) : DEFAULT_FILTERS.window,
		active: active === 'true' ? true : active === 'false' ? false : null,
		page: Number.isInteger(page) && page > 0 ? page : 1
	};
}

/** The URL query for these filters; what equals the default is left out. */
export function filtersToQuery(filters: JobFilters): string {
	const params = new URLSearchParams();
	const sameKinds =
		filters.kinds.length === DEFAULT_FILTERS.kinds.length &&
		filters.kinds.every((k) => DEFAULT_FILTERS.kinds.includes(k));
	if (!sameKinds) {
		for (const kind of filters.kinds.length ? filters.kinds : ['all']) params.append('kind', kind);
	}
	for (const state of filters.states) params.append('state', state);
	for (const product of filters.products) params.append('product', product);
	for (const source of filters.sources) params.append('source', source);
	if (filters.asset) params.set('asset', filters.asset);
	if (filters.protection) params.set('protection', filters.protection);
	if (filters.window !== DEFAULT_FILTERS.window) params.set('window', filters.window);
	if (filters.active !== null) params.set('active', String(filters.active));
	if (filters.page > 1) params.set('page', String(filters.page));
	const query = params.toString();
	return query ? `?${query}` : '';
}

/** The /v1/jobs query for these filters, as of `now`. */
export function apiQuery(filters: JobFilters, now: Date) {
	const hours = WINDOWS[filters.window];
	return {
		kind: filters.kinds.length ? filters.kinds : undefined,
		state: filters.states.length ? filters.states : undefined,
		source_type: filters.products.length ? filters.products : undefined,
		source: filters.sources.length ? filters.sources : undefined,
		asset: filters.asset || undefined,
		protection: filters.protection || undefined,
		active: filters.active ?? undefined,
		since: hours ? new Date(now.getTime() - hours * 3_600_000).toISOString() : undefined
	};
}

/** Tailwind classes for a state's badge: failures stand out (vision 4.4). */
export function stateClass(state: string): string {
	switch (state) {
		case 'succeeded':
			return 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300';
		case 'failed':
		case 'missing':
			return 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300';
		case 'partial':
		case 'warning':
			return 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300';
		case 'running':
		case 'queued':
		case 'waiting':
			return 'bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300';
		case 'unknown':
			return 'bg-fuchsia-100 text-fuchsia-800 dark:bg-fuchsia-950 dark:text-fuchsia-300';
		default:
			return 'bg-muted text-muted-foreground';
	}
}

/** The colour for a state in charts (CSS colour). */
export function stateColour(state: string): string {
	switch (state) {
		case 'succeeded':
			return 'var(--color-emerald-500)';
		case 'failed':
		case 'missing':
			return 'var(--color-red-500)';
		case 'partial':
		case 'warning':
			return 'var(--color-amber-500)';
		case 'running':
		case 'queued':
		case 'waiting':
			return 'var(--color-sky-500)';
		case 'unknown':
			return 'var(--color-fuchsia-500)';
		default:
			return 'var(--color-zinc-400)';
	}
}

/** What each product calls the system a job came from. */
export const SOURCE_NOUN: Record<string, string> = {
	commvault: 'CommCell',
	netbackup: 'Primary server',
	rubrik: 'Rubrik account'
};

/** The heading over a job's raw record: whose record it is, e.g.
 * "CommCell cv-toaster's own record". */
export function recordLabel(sourceType: string | null | undefined, sourceName: string | null | undefined): string {
	const noun = SOURCE_NOUN[sourceType ?? ''] ?? 'Source';
	return sourceName ? `${noun} ${sourceName}'s own record` : `The ${noun.toLowerCase()}'s own record`;
}
