/** A Sankey of two or three columns, laid out by hand (no library): where
 * NetBackup's data comes from on the left, the pools it lands in on the
 * right - and for one primary, the SLP that made each copy between
 * (GET /v1/storage/flows, blt.api.storage_flows).
 *
 * A node is as tall as the larger of what flows in and out of it, every
 * node on one scale; each link is a band from its source to its target,
 * stacked within each node in the order of the nodes at its other end,
 * so bands cross as little as the columns allow. */
import type { components } from '$lib/api/schema';

export type Flows = components['schemas']['StorageFlows'];
export type FlowNode = components['schemas']['FlowNode'];
export type FlowLink = components['schemas']['FlowLink'];
export type FlowKind = FlowLink['kind'];
export type FlowPath = components['schemas']['FlowPath'];

export interface Box {
	node: FlowNode;
	x: number;
	y: number;
	height: number;
	value: number;
}

export interface Band {
	link: FlowLink;
	path: string;
	/** Its thickness at either end. */
	width: number;
	source: Box;
	target: Box;
	/** Where it starts and ends: x, and the y of its top edge. */
	x0: number;
	x1: number;
	sy: number;
	ty: number;
}

export interface Layout {
	boxes: Box[];
	bands: Band[];
	height: number;
}

export interface Size {
	width: number;
	height: number;
	nodeWidth: number;
	/** Gap between nodes in a column. */
	gap: number;
	/** Where each column's nodes start, from the left, by how many columns there are. */
	columns: Record<2 | 3, number[]>;
	/** The thinnest a node or band is drawn, so a small one stays visible. */
	minimum: number;
}

export const SIZE: Size = {
	width: 960,
	height: 420,
	nodeWidth: 14,
	gap: 14,
	columns: { 2: [240, 560], 3: [230, 470, 710] },
	minimum: 2
};

/** The order of a column: by primary, then the largest first. */
function ordered(boxes: Box[]): Box[] {
	return [...boxes].sort(
		(a, b) => a.node.source_name.localeCompare(b.node.source_name) || b.value - a.value
	);
}

export function layout(flows: Pick<Flows, 'nodes' | 'links'>, size: Size = SIZE): Layout {
	const inflow = new Map<string, number>();
	const outflow = new Map<string, number>();
	for (const link of flows.links) {
		outflow.set(link.source, (outflow.get(link.source) ?? 0) + link.bytes);
		inflow.set(link.target, (inflow.get(link.target) ?? 0) + link.bytes);
	}
	const used = flows.nodes.filter((n) => inflow.has(n.id) || outflow.has(n.id));
	const count = columnsOf(flows.nodes);
	const xs = size.columns[count];
	const columns: Box[][] = xs.map((x, c) =>
		ordered(
			used
				.filter((n) => n.column === c)
				.map((node) => ({
					node,
					x,
					y: 0,
					height: 0,
					value: Math.max(inflow.get(node.id) ?? 0, outflow.get(node.id) ?? 0)
				}))
		)
	);

	// One scale for every column: the fullest fills the height.
	const scale = Math.min(
		...columns
			.filter((c) => c.length > 0)
			.map((c) => {
				const room = size.height - size.gap * (c.length - 1);
				const total = c.reduce((sum, b) => sum + b.value, 0);
				return total > 0 ? room / total : Infinity;
			})
	);
	const k = Number.isFinite(scale) ? scale : 0;
	let height = 0;
	const placed = new Map<string, Box>();
	for (const [c, column] of columns.entries()) {
		// Through SLPs, a later column follows the one before it: each node
		// at the mean height of what flows into it, so bands cross less.
		if (count === 3 && c > 0) {
			const mean = new Map<string, number>();
			for (const box of column) {
				let sum = 0;
				let weight = 0;
				for (const link of flows.links) {
					const from = placed.get(link.source);
					if (link.target !== box.node.id || !from) continue;
					sum += (from.y + from.height / 2) * link.bytes;
					weight += link.bytes;
				}
				mean.set(box.node.id, weight > 0 ? sum / weight : Infinity);
			}
			column.sort((a, b) => mean.get(a.node.id)! - mean.get(b.node.id)! || b.value - a.value);
		}
		let y = 0;
		for (const box of column) {
			box.y = y;
			box.height = Math.max(box.value * k, size.minimum);
			y += box.height + size.gap;
			placed.set(box.node.id, box);
		}
		height = Math.max(height, y - size.gap);
	}

	const boxes = new Map(columns.flat().map((b) => [b.node.id, b]));
	const centre = (id: string) => {
		const b = boxes.get(id);
		return b ? b.y + b.height / 2 : 0;
	};
	const links = flows.links.filter((l) => boxes.has(l.source) && boxes.has(l.target));
	// Where the next band starts within each node, on each side.
	const outAt = new Map<string, number>();
	const inAt = new Map<string, number>();
	const byTarget = [...links].sort((a, b) => centre(a.target) - centre(b.target));
	const sourceY = new Map<FlowLink, number>();
	for (const link of byTarget) {
		const box = boxes.get(link.source)!;
		const at = outAt.get(link.source) ?? box.y;
		sourceY.set(link, at);
		outAt.set(link.source, at + Math.max(link.bytes * k, size.minimum));
	}
	const bySource = [...links].sort((a, b) => centre(a.source) - centre(b.source));
	const bands: Band[] = bySource.map((link) => {
		const source = boxes.get(link.source)!;
		const target = boxes.get(link.target)!;
		const width = Math.max(link.bytes * k, size.minimum);
		const ty = inAt.get(link.target) ?? target.y;
		inAt.set(link.target, ty + width);
		const sy = sourceY.get(link)!;
		const x0 = source.x + size.nodeWidth;
		const x1 = target.x;
		const path = bandPath(x0, sy, x1, ty, width);
		return { link, path, width, source, target, x0, x1, sy, ty };
	});
	return { boxes: [...boxes.values()], bands, height };
}

/** A band from (x0, sy) to (x1, ty), `width` thick. */
export function bandPath(x0: number, sy: number, x1: number, ty: number, width: number): string {
	const xm = (x0 + x1) / 2;
	return (
		`M${x0},${sy} C${xm},${sy} ${xm},${ty} ${x1},${ty} ` +
		`L${x1},${ty + width} C${xm},${ty + width} ${xm},${sy + width} ${x0},${sy + width} Z`
	);
}

/** One link, as a key. */
export const edgeKey = (link: Pick<FlowLink, 'source' | 'target' | 'kind'>) =>
	`${link.source}\u0000${link.target}\u0000${link.kind}`;

/** What was clicked: a band, a node, or one path (a row of the table). */
export type Choice = { edge: string } | { node: string } | { path: number };

/** The paths a click picks: those through the band or the node. */
export function chosen(paths: FlowPath[], choice: Choice | null): FlowPath[] {
	if (choice === null) return [];
	if ('path' in choice) return paths[choice.path] ? [paths[choice.path]] : [];
	if ('node' in choice) return paths.filter((p) => p.nodes.includes(choice.node));
	return paths.filter((p) =>
		p.nodes.slice(1).some((to, i) => edgeKey({ source: p.nodes[i], target: to, kind: p.kind }) === choice.edge)
	);
}

/** How much of each link the chosen paths carry. */
export function carried(paths: FlowPath[]): Map<string, number> {
	const out = new Map<string, number>();
	for (const p of paths) {
		p.nodes.slice(1).forEach((to, i) => {
			const key = edgeKey({ source: p.nodes[i], target: to, kind: p.kind });
			out.set(key, (out.get(key) ?? 0) + p.bytes);
		});
	}
	return out;
}

/** How many columns a picture has: three when it goes through SLPs. */
export function columnsOf(nodes: Pick<FlowNode, 'column'>[]): 2 | 3 {
	return nodes.some((n) => n.column === 2) ? 3 : 2;
}

export const KIND_LABEL: Record<FlowKind, string> = {
	backup: 'Backups',
	replicate: 'Replicated to another primary',
	duplicate: 'Duplicated on the same primary'
};

/** Categorical slots 1-3 of the chart palette, in fixed order (app.css). */
export const KIND_COLOUR: Record<FlowKind, string> = {
	backup: 'var(--series-1)',
	replicate: 'var(--series-2)',
	duplicate: 'var(--series-3)'
};

/** The windows the Storage page offers, in days. */
export const WINDOWS = [1, 7, 30] as const;
