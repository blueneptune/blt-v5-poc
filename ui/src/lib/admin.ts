/** For the Administration pages: Prefect's run states in blt's words,
 * schedules people can read, and the CA certificates in a log. */

/** A run in one of these will not change again. */
const FINAL = new Set(['COMPLETED', 'FAILED', 'CANCELLED', 'CRASHED']);

export function isFinal(stateType: string | null | undefined): boolean {
	return FINAL.has(stateType ?? '');
}

/** The job state (lib/jobs.ts) whose colour a run state wears. */
export function runTone(stateType: string | null | undefined): string {
	switch (stateType) {
		case 'COMPLETED':
			return 'succeeded';
		case 'FAILED':
		case 'CRASHED':
			return 'failed';
		case 'RUNNING':
		case 'PENDING':
		case 'CANCELLING':
			return 'running';
		case 'PAUSED':
			return 'warning';
		default:
			return 'other';
	}
}

const pad = (n: number) => String(n).padStart(2, '0');

/** "7 * * * *" -> "hourly at :07". Anything else stays as written. */
export function cronText(cron: string | null | undefined, timezone?: string | null): string {
	if (!cron) return '';
	const parts = cron.trim().split(/\s+/);
	const zone = timezone && timezone !== 'UTC' ? ` ${timezone}` : ' UTC';
	if (parts.length !== 5) return cron;
	const [minute, hour, day, month, weekday] = parts;
	if (!/^\d+$/.test(minute) || day !== '*' || month !== '*' || weekday !== '*') return cron;
	if (hour === '*') return `hourly at :${pad(Number(minute))}`;
	if (/^\d+(,\d+)*$/.test(hour)) {
		const times = hour.split(',').map((h) => `${pad(Number(h))}:${pad(Number(minute))}`);
		const list = times.length > 1 ? `${times.slice(0, -1).join(', ')} and ${times.at(-1)}` : times[0];
		return `daily at ${list}${zone}`;
	}
	return cron;
}

export interface FoundCertificate {
	pem: string;
	sha256: string | null;
	subject: string | null;
	verifies: boolean | null;
}

const PEM = /-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----/g;
const HEADING = /^CA certificate (\d+): (.*) - SHA-256 ([0-9A-F:]+)$/;

/** The certificates blt-netbackup-fetch-ca logged, in order, with what
 * it said about each (blt.flows.netbackup). */
export function certificatesInLog(lines: { message: string }[]): FoundCertificate[] {
	const found: FoundCertificate[] = [];
	let pending: { subject: string | null; sha256: string } | null = null;
	for (const { message } of lines) {
		const heading = HEADING.exec(message);
		if (heading) {
			pending = {
				subject: heading[2] === 'subject unreadable' ? null : heading[2],
				sha256: heading[3]
			};
			continue;
		}
		const pems = message.match(PEM);
		if (pems) {
			for (const pem of pems)
				found.push({ pem: `${pem.trim()}\n`, sha256: pending?.sha256 ?? null, subject: pending?.subject ?? null, verifies: null });
			pending = null;
			continue;
		}
		const verdict = /^CA certificate (\d+) (verifies|does not verify)/.exec(message);
		if (verdict) {
			const certificate = found[Number(verdict[1]) - 1];
			if (certificate) certificate.verifies = verdict[2] === 'verifies';
		}
	}
	return found;
}

/** The address blt-netbackup-fetch-ca found the primary at, when that
 * was not the one asked: port 443, the default 1556 not answering. */
export function answeredAtInLog(lines: { message: string }[]): string | null {
	for (const { message } of lines) {
		const found = /^Answered at (https:\/\/\S+) - use this address\.?$/.exec(message);
		if (found) return found[1];
	}
	return null;
}

export const SOURCE_LABELS: Record<string, string> = {
	commvault: 'Commvault CommCell',
	rubrik: 'Rubrik account',
	netbackup: 'NetBackup primary'
};

export interface ParameterField {
	name: string;
	type: 'string' | 'number' | 'integer' | 'boolean' | 'array' | 'other';
	required: boolean;
	title: string;
	description: string | null;
	default: unknown;
}

type Schema = {
	properties?: Record<string, { type?: string; title?: string; description?: string; default?: unknown; anyOf?: { type?: string }[] }>;
	required?: string[];
};

/** A deployment's parameters, from Prefect's JSON schema of them, as
 * fields for a run-now form. */
export function parameterFields(schema: unknown, defaults: Record<string, unknown> = {}): ParameterField[] {
	const { properties = {}, required = [] } = (schema ?? {}) as Schema;
	return Object.entries(properties).map(([name, property]) => {
		const types = [property.type, ...(property.anyOf ?? []).map((t) => t.type)].filter(
			(t): t is string => !!t && t !== 'null'
		);
		const type = (['string', 'number', 'integer', 'boolean', 'array'].find((t) => types.includes(t)) ??
			'other') as ParameterField['type'];
		return {
			name,
			type,
			required: required.includes(name),
			title: property.title ?? name,
			description: property.description ?? null,
			default: name in defaults ? defaults[name] : property.default
		};
	});
}

/** The form's text back into parameter values; blank means "leave it to
 * the deployment". Throws with a readable message on bad input. */
export function parameterValues(fields: ParameterField[], form: FormData): Record<string, unknown> {
	const values: Record<string, unknown> = {};
	for (const field of fields) {
		const raw = form.get(`param:${field.name}`);
		if (field.type === 'boolean') {
			if (form.has(`present:${field.name}`)) values[field.name] = raw === 'on';
			continue;
		}
		const text = String(raw ?? '').trim();
		if (!text) {
			if (field.required) throw new Error(`${field.title} is needed.`);
			continue;
		}
		if (field.type === 'number' || field.type === 'integer') {
			const n = Number(text);
			if (!Number.isFinite(n) || (field.type === 'integer' && !Number.isInteger(n)))
				throw new Error(`${field.title} must be a number.`);
			values[field.name] = n;
		} else if (field.type === 'array') {
			values[field.name] = text.split(',').map((s) => s.trim()).filter(Boolean);
		} else if (field.type === 'other') {
			try {
				values[field.name] = JSON.parse(text);
			} catch {
				throw new Error(`${field.title} must be JSON.`);
			}
		} else values[field.name] = text;
	}
	return values;
}

/** A worker that collects (GET /v1/admin/collectors), as these pages use it. */
export interface Collector {
	name: string;
	own: boolean;
	status: 'online' | 'offline' | 'paused' | 'never seen';
	last_polled?: string | null;
	sources: string[];
}

/** blt's own worker's work queue; a source naming no collector uses it. */
export const OWN_COLLECTOR = 'default';

/** What to call a collector on these pages. */
export function collectorLabel(name: string | null | undefined): string {
	return !name || name === OWN_COLLECTOR ? 'blt’s own worker' : name;
}

/** The job-state colour (lib/jobs.ts stateClass) a collector's status wears. */
export function collectorTone(status: Collector['status']): string {
	switch (status) {
		case 'online':
			return 'succeeded';
		case 'offline':
			return 'failed';
		case 'paused':
			return 'warning';
		default:
			return 'other';
	}
}

/** The choices for a source's collector: blt's own worker first (value
 * ''), then every remote one by name - with the source's current one
 * even if no collector of that name has ever been seen. */
export function collectorChoices(
	collectors: Collector[],
	current?: string | null
): { value: string; label: string }[] {
	const remote = collectors.filter((c) => !c.own).map((c) => c.name);
	if (current && current !== OWN_COLLECTOR && !remote.includes(current)) remote.push(current);
	const status = new Map(collectors.map((c) => [c.name, c.status]));
	return [
		{ value: '', label: collectorLabel(null) },
		...remote.sort().map((name) => ({
			value: name,
			label: `${name} (${status.get(name) ?? 'never seen'})`
		}))
	];
}
