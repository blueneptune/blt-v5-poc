import type { Role } from '$lib/server/auth';

export interface NavItem {
	label: string;
	href: string;
	/** Shown only to users with this role. */
	role?: Role;
	/** A shorter label, for a phone's narrow bar. */
	short?: string;
	/** Pages listed under this one: a group in the side panel. */
	children?: NavItem[];
}

/** The sections of the UI (docs/vision.md section 4), in order. Each
 * is added here as it is built. */
export const NAV: NavItem[] = [
	{ label: 'Overview', href: '/' },
	{ label: 'Jobs', href: '/jobs' },
	{ label: 'Storage', href: '/storage' },
	{ label: 'Sources', href: '/sources' },
	{
		label: 'Administration',
		short: 'Admin',
		href: '/admin',
		role: 'admin',
		children: [
			{ label: 'Sources', href: '/admin/sources', role: 'admin' },
			{ label: 'Schedules', href: '/admin/schedules', role: 'admin' },
			{ label: 'Runs', href: '/admin/runs', role: 'admin' }
		]
	}
];

/** The navigation one user sees: items, and the children of groups,
 * they lack the role for are left out, and so is a group left empty. */
export function navFor(items: NavItem[], may: (role: Role | undefined) => boolean): NavItem[] {
	return items.flatMap((item) => {
		if (!may(item.role)) return [];
		if (!item.children) return [item];
		const children = navFor(item.children, may);
		return children.length ? [{ ...item, children }] : [];
	});
}

/** Whether a page is the one at `href`, or under it. */
export function isActive(pathname: string, href: string): boolean {
	return href === '/' ? pathname === '/' : pathname === href || pathname.startsWith(`${href}/`);
}
