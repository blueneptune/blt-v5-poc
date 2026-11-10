import { describe, expect, it } from 'vitest';
import { cell, columns, label, layout, show, type Obj } from './record';

describe('field names as words', () => {
	it('splits camelCase, snake_case and capitals', () => {
		expect(label('jobStartTime')).toBe('Job start time');
		expect(label('size_of_application')).toBe('Size of application');
		expect(label('slaDomainName')).toBe('SLA domain name');
		expect(label('clientID')).toBe('Client ID');
		expect(label('O365Mailbox')).toBe('O365 mailbox');
		expect(label('vmUUID')).toBe('VM UUID');
	});
});

describe('values by what they look like', () => {
	it('shows times, sizes, durations and yes/no', () => {
		expect(show('jobStartTime', 1_794_300_000)).toMatch(/2026/);
		expect(show('startTimeMs', 1_794_300_000)).toBe('1,794,300,000');
		expect(show('end_time', '2026-11-10T14:00:00Z')).toMatch(/2026/);
		expect(show('sizeOfApplication', 5_368_709_120)).toBe('5.0 GiB (5,368,709,120)');
		expect(show('elapsed_seconds', 3_725)).toBe('1h 2m');
		expect(show('elapsed_seconds', 42)).toBe('42');
		expect(show('is_aged', false)).toBe('no');
		expect(show('jobId', 12345)).toBe('12345');
		expect(show('client_id', 1004)).toBe('1004');
		expect(show('totalFiles', 12345)).toBe('12,345');
		expect(show('status', 'Completed')).toBe('Completed');
	});
});

describe('a record sorted into fields, sections and tables', () => {
	const paths: Obj[] = [{ ma: 'ma01', lib: 'disk' }, { ma: 'ma02', copy: 'Secondary' }];
	const record: Obj = {
		raw: { a: 1 },
		job_id: 7,
		status: 'Completed',
		pending: null,
		empty: '',
		tags: ['gold', 'nightly'],
		subclient: { clientName: 'host1', appName: 'File System' },
		nothing: { a: null },
		paths
	};

	it('leaves out what is skipped and counts the empty', () => {
		const parts = layout(record, ['raw']);
		expect(parts.fields).toEqual([
			['job_id', '7'],
			['status', 'Completed'],
			['tags', 'gold, nightly']
		]);
		expect(parts.groups.map(([k]) => k)).toEqual(['subclient']);
		expect(parts.tables.map(([k]) => k)).toEqual(['paths']);
		expect(parts.hidden).toBe(3);
	});

	it('gives a table every column any row has', () => {
		expect(columns(paths)).toEqual(['ma', 'lib', 'copy']);
		expect(cell('lib', paths[1].lib)).toBe('');
		expect(cell('x', { nested: 1 })).toBe('{"nested":1}');
	});
});
