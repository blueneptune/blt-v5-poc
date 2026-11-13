import { describe, expect, it } from 'vitest';
import { SIZE, layout, type FlowLink, type FlowNode } from './sankey';

const node = (id: string, column: 0 | 1, source_name = 'a'): FlowNode => ({
	id,
	column,
	label: id,
	source_name,
	storage_id: null
});
const link = (source: string, target: string, bytes: number): FlowLink => ({
	source,
	target,
	kind: 'backup',
	bytes,
	jobs: 1
});

describe('the Sankey layout', () => {
	const nodes = [node('in1', 0), node('in2', 0), node('p1', 1), node('p2', 1), node('idle', 1)];
	const links = [link('in1', 'p1', 300), link('in1', 'p2', 100), link('in2', 'p2', 100)];
	const { boxes, bands, height } = layout({ nodes, links });
	const box = (id: string) => boxes.find((b) => b.node.id === id)!;

	it('leaves out nodes nothing flows through', () => {
		expect(boxes.map((b) => b.node.id).sort()).toEqual(['in1', 'in2', 'p1', 'p2']);
	});

	it('sizes every node on one scale, filling the height of the fuller column', () => {
		expect(box('in1').height / box('in1').value).toBeCloseTo(box('p2').height / box('p2').value);
		expect(height).toBeCloseTo(SIZE.height);
		expect(box('in1').height).toBeCloseTo(2 * box('p2').height); // 400 out, 200 in
	});

	it('puts each column in its own place, the largest node first', () => {
		expect(box('in1').x).toBe(SIZE.columns[0]);
		expect(box('p1').x).toBe(SIZE.columns[1]);
		expect(box('p1').y).toBe(0);
		expect(box('p2').y).toBeGreaterThan(box('p1').y);
	});

	it('stacks the bands within a node without overlap', () => {
		const into = bands.filter((b) => b.link.target === 'p2');
		expect(into).toHaveLength(2);
		const total = into.reduce((sum, b) => sum + b.width, 0);
		expect(total).toBeCloseTo(box('p2').height);
	});

	it('keeps a tiny flow visible', () => {
		const tiny = layout({
			nodes: [node('x', 0), node('y', 1), node('z', 1)],
			links: [link('x', 'y', 1_000_000), link('x', 'z', 1)]
		});
		expect(Math.min(...tiny.bands.map((b) => b.width))).toBe(SIZE.minimum);
	});

	it('draws nothing for no flows', () => {
		expect(layout({ nodes: [node('x', 0)], links: [] })).toEqual({ boxes: [], bands: [], height: 0 });
	});
});
