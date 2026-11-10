/** A product's own job record (Commvault, Rubrik, NetBackup), as people
 * read it: the job page shows it laid out, with the JSON underneath.
 *
 * Nothing here knows one product from another. Field names become words
 * (`sizeOfApplication` -> "Size of application"), and a value is shown by
 * what it looks like and what its field is called: times as times, sizes
 * as sizes, nested objects as their own sections, lists of objects as
 * tables. Empty fields are left out of the layout (the JSON keeps them). */

import { bytes, duration, when } from './format';

export type Plain = string | number | boolean | null;
export type Value = Plain | Value[] | { [key: string]: Value };
export type Obj = { [key: string]: Value };

const ACRONYMS: Record<string, string> = {
	id: 'ID',
	ids: 'IDs',
	sla: 'SLA',
	slp: 'SLP',
	ip: 'IP',
	vm: 'VM',
	vms: 'VMs',
	os: 'OS',
	api: 'API',
	url: 'URL',
	uuid: 'UUID',
	fqdn: 'FQDN',
	tls: 'TLS',
	rsc: 'RSC',
	cdp: 'CDP',
	nas: 'NAS',
	o365: 'O365',
	m365: 'M365',
	ma: 'MA',
	kb: 'KB',
	mb: 'MB',
	gb: 'GB',
	utc: 'UTC'
};

/** A field name as words: `jobStartTime`, `job_start_time`, `JOB-START`
 * all become "Job start time". */
export function label(key: string): string {
	const words = key
		.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
		.replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
		.split(/[\s_\-.]+/)
		.filter(Boolean)
		.map((word) => ACRONYMS[word.toLowerCase()] ?? word.toLowerCase());
	if (words.length === 0) return key;
	const first = words[0];
	words[0] = first === first.toLowerCase() ? first[0].toUpperCase() + first.slice(1) : first;
	return words.join(' ');
}

export function isObj(value: Value | undefined): value is Obj {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function isEmpty(value: Value | undefined): boolean {
	if (value === null || value === undefined || value === '') return true;
	if (Array.isArray(value)) return value.length === 0;
	if (isObj(value)) return Object.values(value).every(isEmpty);
	return false;
}

const ISO = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;
const TIME_KEY = /(time|Time|date|Date|_at|At|timestamp|Timestamp)$/;
const SIZE_KEY = /(bytes|Bytes|size|Size)/;
const ID_KEY = /(^id|_id|Id|ID|Key|_key|Number|_number)$/;
const SECONDS_KEY = /(seconds|Seconds|_secs|Secs|elapsed|Elapsed|duration|Duration)/;

/** Epoch seconds or milliseconds, for a field named like a time. */
function epoch(key: string, value: number): Date | null {
	if (!TIME_KEY.test(key) || !Number.isInteger(value) || value <= 0) return null;
	if (value > 1e9 && value < 1e10) return new Date(value * 1000);
	if (value > 1e12 && value < 1e13) return new Date(value);
	return null;
}

function seconds(value: number): string {
	const start = new Date(0).toISOString();
	return duration(start, new Date(value * 1000).toISOString()) || `${value}s`;
}

/** One plain value as text. */
export function show(key: string, value: Plain): string {
	if (value === null || value === '') return '';
	if (typeof value === 'boolean') return value ? 'yes' : 'no';
	if (typeof value === 'number') {
		const at = epoch(key, value);
		if (at) return when(at.toISOString());
		if (ID_KEY.test(key)) return String(value);
		if (SIZE_KEY.test(key) && value >= 1024 && Number.isInteger(value)) {
			return `${bytes(value)} (${value.toLocaleString()})`;
		}
		if (SECONDS_KEY.test(key) && value >= 60 && !SIZE_KEY.test(key)) return seconds(value);
		return value.toLocaleString();
	}
	if (ISO.test(value) && !Number.isNaN(Date.parse(value))) return when(value);
	return value;
}

export type Layout = {
	/** Plain fields, and lists of plain values (shown joined). */
	fields: [string, string][];
	/** Objects: a section each. */
	groups: [string, Obj][];
	/** Lists of objects: a table each. */
	tables: [string, Obj[]][];
	/** How many empty fields were left out. */
	hidden: number;
};

/** An object's fields sorted into what is shown how. */
export function layout(record: Obj, skip: readonly string[] = []): Layout {
	const out: Layout = { fields: [], groups: [], tables: [], hidden: 0 };
	for (const [key, value] of Object.entries(record)) {
		if (skip.includes(key)) continue;
		if (isEmpty(value)) {
			out.hidden++;
		} else if (isObj(value)) {
			out.groups.push([key, value]);
		} else if (Array.isArray(value)) {
			if (value.every(isObj)) out.tables.push([key, value as Obj[]]);
			else out.fields.push([key, value.map((v) => (isObj(v) || Array.isArray(v) ? JSON.stringify(v) : show(key, v))).join(', ')]);
		} else {
			out.fields.push([key, show(key, value)]);
		}
	}
	return out;
}

/** The columns of a table of objects: every key any row has, in first-seen
 * order, without the ones empty in every row. A cell that is itself an
 * object or list is shown as JSON. */
export function columns(rows: Obj[]): string[] {
	const keys: string[] = [];
	for (const row of rows) for (const key of Object.keys(row)) if (!keys.includes(key)) keys.push(key);
	return keys.filter((key) => rows.some((row) => !isEmpty(row[key])));
}

export function cell(key: string, value: Value | undefined): string {
	if (value === undefined || isEmpty(value)) return '';
	if (isObj(value) || Array.isArray(value)) return JSON.stringify(value);
	return show(key, value);
}
