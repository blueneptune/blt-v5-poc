/**
 * Who is using the UI, and what they may do - the one place sign-in will
 * plug in.
 *
 * Today there is no sign-in: every request is the lab user, who holds
 * every role. When Zitadel arrives (docs/vision.md 4.1), `currentUser`
 * reads the session instead, and nothing else changes: every page already
 * asks `allowed` / `requireRole`, and the navigation is already filtered
 * by role. Until then the padlocks are fitted but never locked.
 */
import { error, type RequestEvent } from '@sveltejs/kit';

/** Roles from docs/common-jobs.md decision 3: engineering sees blt's own
 * collection runs, operations does not; admin manages users and sources. */
export const ROLES = ['operations', 'engineering', 'admin'] as const;
export type Role = (typeof ROLES)[number];

export interface User {
	id: string;
	name: string;
	roles: Role[];
	/** False until sign-in exists: the UI says so instead of offering "Sign out". */
	signedIn: boolean;
}

const LAB_USER: User = { id: 'lab', name: 'Lab', roles: [...ROLES], signedIn: false };

export function currentUser(_event: RequestEvent): User {
	return LAB_USER;
}

export function allowed(user: User, role: Role | undefined): boolean {
	return role === undefined || user.roles.includes(role);
}

/** In a load function or action: stop here unless the user has the role. */
export function requireRole(user: User, role: Role): void {
	if (!allowed(user, role)) error(403, `This needs the ${role} role.`);
}
