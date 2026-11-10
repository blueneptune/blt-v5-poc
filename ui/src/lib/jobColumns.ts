/**
 * The jobs table's columns: every field of a job (CommonJobOut) a viewer
 * may show, which are shown at first, and how wide each is - and the
 * viewer's own choice of those, kept in this browser.
 */

export interface JobColumn {
	id: string;
	label: string;
	/** What it shows, for the column picker. */
	description: string;
	/** Starting width, in px. */
	width: number;
	/** Shown until the viewer chooses otherwise. */
	shown: boolean;
	align?: 'right';
}

export const JOB_COLUMNS: JobColumn[] = [
	{ id: 'state', label: 'State', description: "blt's state, the product's own word on hover", width: 104, shown: true },
	{ id: 'asset', label: 'Asset', description: 'What it ran on, with why it failed or warned', width: 256, shown: true },
	{ id: 'message', label: 'Message', description: 'Why it failed or warned, as its own column', width: 280, shown: false },
	{ id: 'asset_type', label: 'Asset type', description: 'What sort of thing the asset is', width: 160, shown: false },
	{ id: 'protection', label: 'Protection', description: 'Plan, SLA domain or policy that made it run', width: 160, shown: true },
	{ id: 'source', label: 'Source', description: 'The CommCell, account or primary, and its product', width: 176, shown: true },
	{ id: 'kind', label: 'Kind', description: 'Backup, restore, copy, ...', width: 96, shown: true },
	{ id: 'product_status', label: 'Product status', description: "The product's own word for its status", width: 136, shown: false },
	{ id: 'job_id', label: 'Job ID', description: "The product's own id for the job", width: 112, shown: false },
	{ id: 'started', label: 'Started', description: 'When it started', width: 176, shown: true },
	{ id: 'ended', label: 'Ended', description: 'When it ended', width: 176, shown: false },
	{ id: 'took', label: 'Took', description: 'How long it ran (so far, if running)', width: 96, shown: true },
	{ id: 'written', label: 'Written', description: 'Written to backup storage', width: 104, shown: true, align: 'right' },
	{ id: 'protected', label: 'Protected', description: 'The data it covered', width: 104, shown: false, align: 'right' },
	{ id: 'reduction', label: 'Reduction', description: 'Protected for each byte written (e.g. 12×)', width: 96, shown: false, align: 'right' },
	{ id: 'attempt', label: 'Attempt', description: 'Which try this is', width: 80, shown: false, align: 'right' },
	{ id: 'updated', label: 'Updated', description: 'When the product last changed it', width: 176, shown: false },
	{ id: 'collected', label: 'Collected', description: 'When blt last collected it', width: 176, shown: false }
];

export const MIN_WIDTH = 56;
export const MAX_WIDTH = 800;

export interface ColumnPrefs {
	/** Ids of the columns shown, in JOB_COLUMNS order. */
	shown: string[];
	/** Widths the viewer dragged to, by id; the rest keep their own. */
	widths: Record<string, number>;
}

const byId = new Map(JOB_COLUMNS.map((c) => [c.id, c]));

export function defaultPrefs(): ColumnPrefs {
	return { shown: JOB_COLUMNS.filter((c) => c.shown).map((c) => c.id), widths: {} };
}

function clamp(px: number): number {
	return Math.round(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, px)));
}

/** Prefs from what was stored: anything unknown or out of range is
 * dropped, and nothing stored (or nothing usable) is the default. */
export function parsePrefs(text: string | null): ColumnPrefs {
	if (!text) return defaultPrefs();
	try {
		const raw = JSON.parse(text) as Partial<ColumnPrefs>;
		const wanted = new Set(Array.isArray(raw.shown) ? raw.shown : []);
		const shown = JOB_COLUMNS.filter((c) => wanted.has(c.id)).map((c) => c.id);
		const widths: Record<string, number> = {};
		for (const [id, px] of Object.entries(raw.widths ?? {})) {
			if (byId.has(id) && typeof px === 'number' && Number.isFinite(px)) widths[id] = clamp(px);
		}
		return { shown: shown.length ? shown : defaultPrefs().shown, widths };
	} catch {
		return defaultPrefs();
	}
}

/** Show or hide a column; the last one shown stays. */
export function toggleColumn(prefs: ColumnPrefs, id: string): ColumnPrefs {
	if (!byId.has(id)) return prefs;
	const on = prefs.shown.includes(id);
	if (on && prefs.shown.length === 1) return prefs;
	const wanted = new Set(on ? prefs.shown.filter((c) => c !== id) : [...prefs.shown, id]);
	return { ...prefs, shown: JOB_COLUMNS.filter((c) => wanted.has(c.id)).map((c) => c.id) };
}

export function setWidth(prefs: ColumnPrefs, id: string, px: number): ColumnPrefs {
	return { ...prefs, widths: { ...prefs.widths, [id]: clamp(px) } };
}

/** Back to the column's own width. */
export function resetWidth(prefs: ColumnPrefs, id: string): ColumnPrefs {
	const widths = { ...prefs.widths };
	delete widths[id];
	return { ...prefs, widths };
}

export function widthOf(prefs: ColumnPrefs, column: JobColumn): number {
	return prefs.widths[column.id] ?? column.width;
}

export function shownColumns(prefs: ColumnPrefs): JobColumn[] {
	return prefs.shown.map((id) => byId.get(id)).filter((c): c is JobColumn => !!c);
}

/** Protected for each byte written, e.g. "12×"; blank without both. */
export function reduction(protectedBytes: number | null | undefined, written: number | null | undefined): string {
	if (!protectedBytes || !written) return '';
	const ratio = protectedBytes / written;
	return `${ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1)}×`;
}
