import { describe, expect, it } from 'vitest';
import { NAV, isActive, navFor, type NavItem } from './nav';

describe('navFor', () => {
	it('gives an admin the Administration group with its pages', () => {
		const nav = navFor(NAV, () => true);
		const admin = nav.find((item) => item.href === '/admin');
		expect(admin?.children?.map((c) => c.label)).toEqual(['Sources', 'Schedules', 'Runs']);
	});

	it('leaves the group out for someone without the role', () => {
		const nav = navFor(NAV, (role) => role !== 'admin');
		expect(nav.map((item) => item.href)).toEqual(['/', '/jobs', '/storage', '/sources']);
	});

	it('drops a group whose pages are all out of reach', () => {
		const items: NavItem[] = [
			{ label: 'Group', href: '/g', children: [{ label: 'One', href: '/g/1', role: 'admin' }] }
		];
		expect(navFor(items, (role) => role === undefined)).toEqual([]);
	});
});

describe('isActive', () => {
	it('matches a page and the pages under it, not a lookalike', () => {
		expect(isActive('/admin/runs/abc', '/admin/runs')).toBe(true);
		expect(isActive('/admin', '/admin')).toBe(true);
		expect(isActive('/administer', '/admin')).toBe(false);
		expect(isActive('/jobs', '/')).toBe(false);
		expect(isActive('/', '/')).toBe(true);
	});

	it('keeps the two Sources pages apart', () => {
		expect(isActive('/admin/sources', '/sources')).toBe(false);
		expect(isActive('/sources', '/admin/sources')).toBe(false);
	});
});
