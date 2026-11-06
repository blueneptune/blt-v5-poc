/** For the Administration pages' server side: only admins, and the
 * API's own words when it refuses something. */
import { fail } from '@sveltejs/kit';
import { requireRole, type User } from './auth';

export function requireAdmin(user: User | null): asserts user is User {
	requireRole(user, 'admin');
}

/** The API's reason for a refusal, for showing beside the form. */
export function reason(error: unknown, response: Response): string {
	if (error && typeof error === 'object' && 'detail' in error) {
		const detail = (error as { detail: unknown }).detail;
		if (typeof detail === 'string') return detail;
		if (Array.isArray(detail))
			return detail
				.map((d) => {
					const where = Array.isArray(d?.loc) ? d.loc.filter((p: unknown) => p !== 'body').join('.') : '';
					const message = String(d?.msg ?? d).replace(/^Value error, /, '');
					return where ? `${where}: ${message}` : message;
				})
				.join('; ');
	}
	return `${response.status} ${response.statusText}`;
}

/** A failed action, with the API's reason. */
export function refused<E extends Record<string, unknown> = Record<never, never>>(
	result: { error?: unknown; response: Response },
	extra?: E
) {
	const status = result.response.status >= 500 ? 502 : result.response.status;
	return fail(status, { ...(extra ?? ({} as E)), message: reason(result.error, result.response) });
}
