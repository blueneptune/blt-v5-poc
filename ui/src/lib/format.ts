/** Numbers and times as people read them. Times are shown in the
 * viewer's own zone; the API's are UTC. */

const UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'];

export function bytes(value: number | null | undefined): string {
	if (value === null || value === undefined) return '';
	let n = value;
	let unit = 0;
	while (n >= 1024 && unit < UNITS.length - 1) {
		n /= 1024;
		unit++;
	}
	return `${unit === 0 ? n : n.toFixed(n < 10 ? 1 : 0)} ${UNITS[unit]}`;
}

export function duration(start: string | null | undefined, end: string | null | undefined): string {
	if (!start) return '';
	const ms = (end ? Date.parse(end) : Date.now()) - Date.parse(start);
	if (!Number.isFinite(ms) || ms < 0) return '';
	const s = Math.round(ms / 1000);
	if (s < 60) return `${s}s`;
	const m = Math.floor(s / 60);
	if (m < 60) return `${m}m ${s % 60}s`;
	const h = Math.floor(m / 60);
	if (h < 48) return `${h}h ${m % 60}m`;
	return `${Math.floor(h / 24)}d ${h % 24}h`;
}

const dateTime = new Intl.DateTimeFormat(undefined, {
	year: 'numeric',
	month: 'short',
	day: 'numeric',
	hour: '2-digit',
	minute: '2-digit'
});

export function when(value: string | null | undefined): string {
	return value ? dateTime.format(new Date(value)) : '';
}

export function ago(value: string | null | undefined, now: Date = new Date()): string {
	if (!value) return '';
	const minutes = Math.round((now.getTime() - Date.parse(value)) / 60_000);
	if (minutes < 1) return 'just now';
	if (minutes < 60) return `${minutes} min ago`;
	const hours = Math.round(minutes / 60);
	if (hours < 48) return `${hours} h ago`;
	return `${Math.round(hours / 24)} days ago`;
}
