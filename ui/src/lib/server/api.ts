/**
 * blt's API, called from the UI's server side only: the browser never
 * talks to it, so the key (and later each user's token) never reaches it.
 *
 * Settings, from the environment:
 *   BLT_API_URL  where the API is, e.g. http://localhost:8188
 *   BLT_API_KEY  its X-API-Key
 *
 * Types come from docs/openapi.json (`npm run api:types`), so a call that
 * no longer matches the API fails `npm run check`.
 */
import { env } from '$env/dynamic/private';
import { error } from '@sveltejs/kit';
import createClient from 'openapi-fetch';
import type { paths } from '$lib/api/schema';
import type { User } from './auth';

export type ApiClient = ReturnType<typeof createClient<paths>>;

export function apiFor(_user: User, fetch: typeof globalThis.fetch): ApiClient {
	const configured = Boolean(env.BLT_API_URL && env.BLT_API_KEY);
	// With sign-in, the user's own token goes here instead, so the API
	// decides what each user may see.
	return createClient<paths>({
		baseUrl: env.BLT_API_URL ?? '',
		headers: { 'X-API-Key': env.BLT_API_KEY ?? '' },
		// Unconfigured, a page that needs the API fails with why; one that
		// does not (/healthz) still answers.
		fetch: configured
			? fetch
			: () => error(500, 'BLT_API_URL and BLT_API_KEY must be set for the UI to reach the API.')
	});
}

/** The data of an API answer, or the page fails with the API's own words. */
export function must<T>(result: { data?: T; error?: unknown; response: Response }): T {
	if (result.data !== undefined) return result.data;
	const detail =
		result.error && typeof result.error === 'object' && 'detail' in result.error
			? String((result.error as { detail: unknown }).detail)
			: result.response.statusText;
	error(result.response.status === 404 ? 404 : 502, `blt API: ${detail}`);
}
