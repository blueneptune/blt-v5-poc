import { describe, expect, it } from 'vitest';
import { certificatesInLog, cronText, isFinal, runTone } from './admin';

describe('schedules', () => {
	it('read as words where they are simple', () => {
		expect(cronText('7 * * * *')).toBe('hourly at :07');
		expect(cronText('37 5,17 * * *', 'UTC')).toBe('daily at 05:37 and 17:37 UTC');
		expect(cronText('0 2 * * *', 'Europe/London')).toBe('daily at 02:00 Europe/London');
		expect(cronText('*/5 * * * 1-5')).toBe('*/5 * * * 1-5');
		expect(cronText(null)).toBe('');
	});
});

describe('run states', () => {
	it('end, and wear a job colour', () => {
		expect(isFinal('COMPLETED') && isFinal('CRASHED')).toBe(true);
		expect(isFinal('RUNNING') || isFinal('SCHEDULED') || isFinal(null)).toBe(false);
		expect(runTone('FAILED')).toBe('failed');
		expect(runTone('SCHEDULED')).toBe('other');
	});
});

describe('certificates in a log', () => {
	const pem = '-----BEGIN CERTIFICATE-----\nMIIBabc\n-----END CERTIFICATE-----';

	it('come with their fingerprint and verdict', () => {
		const found = certificatesInLog([
			{ message: 'https://nbu01:1556/netbackup gave 2 CA certificate(s)' },
			{ message: 'CA certificate 1: CN=NetBackup CA - SHA-256 AB:CD' },
			{ message: pem },
			{ message: "CA certificate 1 verifies this primary's TLS certificate." },
			{ message: 'CA certificate 2: subject unreadable - SHA-256 EF:01' },
			{ message: pem },
			{ message: 'CA certificate 2 does not verify it: hostname mismatch' }
		]);
		expect(found).toEqual([
			{ pem: `${pem}\n`, sha256: 'AB:CD', subject: 'CN=NetBackup CA', verifies: true },
			{ pem: `${pem}\n`, sha256: 'EF:01', subject: null, verifies: false }
		]);
	});
});

import { parameterFields, parameterValues } from './admin';

describe('run-now parameters', () => {
	const schema = {
		properties: {
			commcell: { type: 'string', title: 'Commcell' },
			wait_minutes: { type: 'number', default: 30 },
			commcells: { anyOf: [{ type: 'array' }, { type: 'null' }], default: null },
			full: { type: 'boolean', default: false }
		},
		required: ['commcell']
	};

	it('become fields with their defaults', () => {
		const fields = parameterFields(schema, { wait_minutes: 50 });
		expect(fields.map((f) => [f.name, f.type, f.required, f.default])).toEqual([
			['commcell', 'string', true, undefined],
			['wait_minutes', 'number', false, 50],
			['commcells', 'array', false, null],
			['full', 'boolean', false, false]
		]);
	});

	it('come back as values, blank left to the deployment', () => {
		const fields = parameterFields(schema);
		const form = new FormData();
		form.set('param:commcell', ' cv-toaster ');
		form.set('param:wait_minutes', '');
		form.set('param:commcells', 'a, b');
		form.set('present:full', '1');
		form.set('param:full', 'on');
		expect(parameterValues(fields, form)).toEqual({ commcell: 'cv-toaster', commcells: ['a', 'b'], full: true });
		form.set('param:commcell', '');
		expect(() => parameterValues(fields, form)).toThrow('Commcell is needed.');
		form.set('param:commcell', 'x');
		form.set('param:wait_minutes', 'soon');
		expect(() => parameterValues(fields, form)).toThrow('must be a number');
	});
});
