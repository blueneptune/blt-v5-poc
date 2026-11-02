import type { Role } from '$lib/server/auth';

export interface NavItem {
	label: string;
	href: string;
	/** Shown only to users with this role (all of them, until sign-in). */
	role?: Role;
}

/** The sections of the UI (docs/vision.md section 4), in order. Each
 * is added here as it is built. */
export const NAV: NavItem[] = [
	{ label: 'Overview', href: '/' },
	{ label: 'Jobs', href: '/jobs' },
	{ label: 'Sources', href: '/sources' }
];
