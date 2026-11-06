import type { Role } from '$lib/server/auth';

export interface NavItem {
	label: string;
	href: string;
	/** Shown only to users with this role. */
	role?: Role;
	/** The heading it is listed under; none for the main sections. */
	section?: string;
}

/** The sections of the UI (docs/vision.md section 4), in order. Each
 * is added here as it is built. */
export const NAV: NavItem[] = [
	{ label: 'Overview', href: '/' },
	{ label: 'Jobs', href: '/jobs' },
	{ label: 'Storage', href: '/storage' },
	{ label: 'Sources', href: '/sources' },
	{ label: 'Sources', href: '/admin/sources', role: 'admin', section: 'Administration' },
	{ label: 'Schedules', href: '/admin/schedules', role: 'admin', section: 'Administration' },
	{ label: 'Runs', href: '/admin/runs', role: 'admin', section: 'Administration' }
];
