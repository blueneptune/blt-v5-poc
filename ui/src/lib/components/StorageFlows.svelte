<script lang="ts">
	// Where NetBackup's data goes (GET /v1/storage/flows): backups and
	// copies on the left, the pools they land in on the right. Each pool
	// is drawn as a meter - its fill is how full it is, in its health's
	// colour, with an icon and a word once it needs attention - so a pool
	// filling up shows with what is filling it.
	//
	// For one primary (flows.primary): three columns - the pools its copies
	// are read from, the SLP that made each, the pools they land in - with
	// pools as meters on both sides.
	//
	// Click a band, a pool or an SLP (or press Enter on it) to light the
	// paths through it, end to end, and only their share of each band;
	// click it again, the background, or press Escape to clear.
	import { CircleAlert, TriangleAlert } from '@lucide/svelte';
	import { bytes } from '$lib/format';
	import {
		KIND_COLOUR,
		KIND_LABEL,
		SIZE,
		bandPath,
		carried,
		chosen,
		columnsOf,
		edgeKey,
		layout,
		type Band,
		type Box,
		type Choice,
		type FlowKind,
		type FlowPath,
		type Flows
	} from '$lib/sankey';
	import { HEALTH_LABEL, fullIn, health, healthColour, usedFraction, type Target } from '$lib/storage';

	let { flows }: { flows: Flows } = $props();

	const drawn = $derived(layout(flows));
	const targets = $derived(new Map(flows.targets.map((t) => [t.id, t])));
	const target = (box: Box): Target | undefined =>
		box.node.storage_id ? targets.get(box.node.storage_id) : undefined;
	const kinds = $derived([...new Set(flows.links.map((l) => l.kind))] as FlowKind[]);
	const idle = $derived(
		flows.targets.filter((t) => !drawn.boxes.some((b) => b.node.storage_id === t.id))
	);
	const names = $derived(new Map(drawn.boxes.map((b) => [b.node.id, b.node.label])));
	const three = $derived(columnsOf(flows.nodes) === 3);
	const last = $derived(three ? 2 : 1);
	// A pool (or a storage unit in none) is a meter; where data comes from,
	// and an SLP, a plain bar.
	const isMeter = (box: Box) => box.node.column === last || (three && box.node.column === 0);
	const inColumn = (c: number) => drawn.boxes.filter((b) => b.node.column === c).length;
	const TOP = 8;
	const H = $derived(drawn.height + TOP * 2);

	let hover: { x: number; y: number; lines: string[] } | null = $state(null);
	let active: Band | null = $state(null);

	let choice: Choice | null = $state(null);
	// Another window or primary: nothing chosen.
	$effect(() => {
		void flows;
		choice = null;
	});
	const picked = $derived(chosen(flows.paths, choice));
	const lit = $derived(carried(picked));
	const litNodes = $derived(new Set(picked.flatMap((p) => p.nodes)));
	const same = (a: Choice | null, b: Choice) => JSON.stringify(a) === JSON.stringify(b);
	function choose(next: Choice) {
		choice = same(choice, next) ? null : next;
	}
	function onKey(event: KeyboardEvent, next: Choice) {
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			choose(next);
		}
	}
	const pathLabel = (p: FlowPath) => p.nodes.map((n) => names.get(n) ?? n).join(' → ');
	const pickedBytes = $derived(picked.reduce((sum, p) => sum + p.bytes, 0));

	function at(event: MouseEvent, lines: string[]) {
		const svg = (event.currentTarget as SVGElement).ownerSVGElement ?? (event.currentTarget as SVGSVGElement);
		const rect = svg.getBoundingClientRect();
		hover = { x: event.clientX - rect.left, y: event.clientY - rect.top, lines };
	}
	function bandLines(band: Band): string[] {
		return [
			`${band.source.node.label} → ${band.target.node.label}`,
			`${KIND_LABEL[band.link.kind]}: ${bytes(band.link.bytes)} in ${band.link.jobs} job${band.link.jobs === 1 ? '' : 's'}`
		];
	}
	function boxLines(box: Box): string[] {
		const t = target(box);
		const what = box.node.column === 0 ? 'Sent' : box.node.column === last ? 'Received' : 'Copied';
		const lines = [
			three && box.node.column === 1 ? `SLP ${box.node.label}` : `${box.node.label} (${box.node.source_name})`,
			`${what}: ${bytes(box.value)}`
		];
		if (t) {
			const used = usedFraction(t);
			if (used !== null) lines.push(`${Math.round(used * 100)}% used: ${bytes(t.latest?.used_bytes)} of ${bytes(t.latest?.capacity_bytes)}`);
			const full = fullIn(t);
			if (full) lines.push(`Full in: ${full}`);
		}
		return lines;
	}
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && (choice = null)} />

{#if drawn.boxes.length === 0}
	<p class="text-muted-foreground text-sm">
		{flows.primary
			? `No copies (duplications or replications) ended on ${flows.primary} in this window.`
			: 'No NetBackup backups or copies ended in this window.'}
	</p>
{:else}
	<ul class="flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="Legend">
		{#each kinds as kind (kind)}
			<li class="flex items-center gap-1.5">
				<span class="inline-block h-2.5 w-4 rounded-sm" style="background: {KIND_COLOUR[kind]}"></span>
				{KIND_LABEL[kind]}
			</li>
		{/each}
		<li class="text-muted-foreground">Pools: the bar fills as the pool does. Click a band or a pool to follow it.</li>
	</ul>

	<div class="relative mt-3 overflow-x-auto">
		<!-- A click on the background clears; for the keyboard, Escape and Clear do. -->
		<!-- svelte-ignore a11y_no_noninteractive_element_interactions, a11y_click_events_have_key_events -->
		<svg
			viewBox="0 0 {SIZE.width} {H}"
			class="block w-full min-w-[720px]"
			role="group"
			aria-label="{three
				? `${flows.primary}'s copies: from ${inColumn(0)} pools through ${inColumn(1)} SLPs into ${inColumn(2)} pools`
				: `Where NetBackup's data went: ${flows.links.length} flows into ${inColumn(1)} pools`}. The table below has the same figures."
			onmouseleave={() => {
				hover = null;
				active = null;
			}}
			onclick={(e) => {
				if (e.target === e.currentTarget) choice = null;
			}}
		>
			<g transform="translate(0 {TOP})">
				{#each drawn.bands as band (band.link.source + band.link.target + band.link.kind)}
					{@const key = edgeKey(band.link)}
					<path
						d={band.path}
						fill={KIND_COLOUR[band.link.kind]}
						fill-opacity={choice ? 0.12 : active === null ? 0.4 : active === band ? 0.75 : 0.15}
						stroke="var(--card)"
						stroke-width="1"
						class="cursor-pointer outline-none"
						role="button"
						tabindex="0"
						aria-label="{bandLines(band).join(': ')}"
						aria-pressed={same(choice, { edge: key })}
						onclick={() => choose({ edge: key })}
						onkeydown={(e) => onKey(e, { edge: key })}
						onmousemove={(e) => {
							active = band;
							at(e, bandLines(band));
						}}
					/>
				{/each}
				{#if choice}
					<!-- The chosen paths' share of each band, from its top edge. -->
					{#each drawn.bands as band (band.link.source + band.link.target + band.link.kind)}
						{@const share = lit.get(edgeKey(band.link)) ?? 0}
						{#if share > 0}
							<path
								d={bandPath(band.x0, band.sy, band.x1, band.ty, Math.max((band.width * share) / band.link.bytes, SIZE.minimum))}
								fill={KIND_COLOUR[band.link.kind]}
								fill-opacity="0.85"
								pointer-events="none"
							/>
						{/if}
					{/each}
				{/if}

				{#each drawn.boxes as box (box.node.id)}
					{@const t = target(box)}
					{@const used = t ? usedFraction(t) : null}
					{@const state = t ? health(t) : 'unknown'}
					<g
						class="cursor-pointer outline-none"
						role="button"
						tabindex="0"
						aria-label={boxLines(box).join('; ')}
						aria-pressed={same(choice, { node: box.node.id })}
						opacity={choice && !litNodes.has(box.node.id) ? 0.35 : 1}
						onclick={() => choose({ node: box.node.id })}
						onkeydown={(e) => onKey(e, { node: box.node.id })}
						onmousemove={(e) => {
							active = null;
							at(e, boxLines(box));
						}}
					>
						{#if !isMeter(box)}
							{@const left = box.node.column === 0}
							<rect x={box.x} y={box.y} width={SIZE.nodeWidth} height={box.height} rx="3" fill="var(--chart-reference)" />
							<!-- An SLP's name sits over the bands it sends on: haloed in the card's colour. -->
							<text
								x={left ? box.x - 8 : box.x + SIZE.nodeWidth + 6}
								y={box.y + box.height / 2}
								text-anchor={left ? 'end' : 'start'}
								dominant-baseline="middle"
								class="fill-foreground text-[12px]"
								stroke={left ? undefined : 'var(--card)'}
								stroke-width={left ? undefined : 4}
								stroke-linejoin="round"
								paint-order="stroke"
							>
								{box.node.label}
								<tspan class="fill-muted-foreground"> {bytes(box.value)}</tspan>
							</text>
						{:else}
							<!-- A pool: a meter, filled from the bottom as the pool is. -->
							<rect x={box.x} y={box.y} width={SIZE.nodeWidth} height={box.height} rx="3" fill="var(--meter-track)" />
							{#if used !== null}
								<rect
									x={box.x}
									y={box.y + box.height * (1 - used)}
									width={SIZE.nodeWidth}
									height={box.height * used}
									rx="3"
									fill={healthColour(state)}
								/>
							{/if}
							{@const alert = state === 'critical' || state === 'warning'}
							{@const full = t ? fullIn(t) : ''}
							{@const left = box.node.column === 0}
							{@const gap = alert ? 26 : 8}
							{@const tx = left ? box.x - gap : box.x + SIZE.nodeWidth + gap}
							{@const ix = left ? box.x - 20 : box.x + SIZE.nodeWidth + 6}
							{@const twoLines = box.height >= 26}
							<text
								x={tx}
								y={box.y + box.height / 2}
								dy={twoLines ? '-0.15em' : '0.35em'}
								text-anchor={left ? 'end' : 'start'}
								class="fill-foreground text-[12px]"
							>
								<tspan font-weight="500">{box.node.label}</tspan>
								{#if used !== null}
									<tspan class="fill-muted-foreground"> · {Math.round(used * 100)}% used</tspan>
								{/if}
								{#if alert}
									<tspan font-weight="500"> · {HEALTH_LABEL[state]}</tspan>
								{/if}
								{#if twoLines}
									<tspan x={tx} dy="1.3em" class="fill-muted-foreground text-[11px]">
										{box.node.source_name} · {bytes(box.value)} {left ? 'out' : 'in'}{full && full !== 'not filling' ? ` · full in ${full}` : ''}
									</tspan>
								{/if}
							</text>
							{#if state === 'critical'}
								<CircleAlert x={ix} y={box.y + box.height / 2 - 7} width="14" height="14" color={healthColour(state)} />
							{:else if state === 'warning'}
								<TriangleAlert x={ix} y={box.y + box.height / 2 - 7} width="14" height="14" color={healthColour(state)} />
							{/if}
						{/if}
					</g>
				{/each}
			</g>
		</svg>
		{#if hover}
			<div
				class="bg-card text-card-foreground pointer-events-none absolute z-10 rounded-md border px-2.5 py-1.5 text-xs shadow-md"
				style="left: {Math.min(hover.x + 12, 9999)}px; top: {hover.y + 12}px"
			>
				{#each hover.lines as line, i (i)}
					<div class={i === 0 ? 'font-medium' : 'text-muted-foreground tabular-nums'}>{line}</div>
				{/each}
			</div>
		{/if}
	</div>

	{#if flows.unmatched_replications > 0}
		<p class="text-muted-foreground mt-2 text-xs">
			{flows.unmatched_replications} replication job{flows.unmatched_replications === 1 ? '' : 's'}
			not yet matched to an import on the receiving primary: drawn to the storage unit the job names. Is that
			primary collected?
		</p>
	{/if}
	{#if choice}
		<div class="bg-muted/40 mt-2 rounded-md border px-3 py-2 text-xs" aria-live="polite">
			<div class="flex items-baseline justify-between gap-2">
				<p class="font-medium">
					{picked.length} path{picked.length === 1 ? '' : 's'}, {bytes(pickedBytes)}
				</p>
				<button type="button" class="text-muted-foreground hover:text-foreground underline" onclick={() => (choice = null)}>
					Clear
				</button>
			</div>
			<ul class="mt-1 space-y-0.5">
				{#each picked.slice(0, 8) as p (p.nodes.join() + p.kind)}
					<li class="flex flex-wrap items-baseline gap-x-2">
						<span class="inline-block h-2 w-3 rounded-sm" style="background: {KIND_COLOUR[p.kind]}"></span>
						<span>{pathLabel(p)}</span>
						<span class="text-muted-foreground tabular-nums">{bytes(p.bytes)} in {p.jobs} job{p.jobs === 1 ? '' : 's'}</span>
					</li>
				{/each}
			</ul>
			{#if picked.length > 8}
				<p class="text-muted-foreground mt-1">and {picked.length - 8} more - in the table below.</p>
			{/if}
		</div>
	{/if}
	{#if flows.unknown_sources > 0}
		<p class="text-muted-foreground mt-2 text-xs">
			{flows.unknown_sources} copy job{flows.unknown_sources === 1 ? '' : 's'} named no source storage, and
			neither its SLP nor its backup job told where it read from: drawn from "unknown". Are the primary's SLPs
			collected (blt-netbackup check)?
		</p>
	{/if}
	{#if idle.length > 0 && !flows.primary}
		<p class="text-muted-foreground mt-2 text-xs">
			Nothing written in this window: {idle.map((t) => `${t.name} (${t.source_name})`).join(', ')}
		</p>
	{/if}

	<details class="mt-3 text-sm">
		<summary class="text-muted-foreground cursor-pointer text-xs">Show as a table</summary>
		<div class="mt-2 overflow-x-auto">
			<table class="w-full min-w-[560px] text-sm">
				<thead class="bg-muted/60 text-muted-foreground text-left text-xs">
					<tr>
						<th class="px-3 py-2 font-medium">From</th>
						{#if three}<th class="px-3 py-2 font-medium">SLP</th>{/if}
						<th class="px-3 py-2 font-medium">To</th>
						<th class="px-3 py-2 font-medium">How</th>
						<th class="px-3 py-2 text-right font-medium">Data</th>
						<th class="px-3 py-2 text-right font-medium">Jobs</th>
						<th class="px-3 py-2"><span class="sr-only">Highlight</span></th>
					</tr>
				</thead>
				<tbody class="striped">
					<!-- Each way end to end (flows.paths: biggest first). -->
					{#each flows.paths as p, i (p.nodes.join() + p.kind)}
						<tr class="border-t {same(choice, { path: i }) ? 'bg-accent' : ''}">
							{#each p.nodes as n (n)}
								<td class="px-3 py-2">{names.get(n) ?? n}</td>
							{/each}
							<td class="px-3 py-2">{KIND_LABEL[p.kind]}</td>
							<td class="px-3 py-2 text-right tabular-nums">{bytes(p.bytes)}</td>
							<td class="px-3 py-2 text-right tabular-nums">{p.jobs}</td>
							<td class="px-3 py-2 text-right">
								<button
									type="button"
									class="text-muted-foreground hover:text-foreground text-xs underline"
									aria-pressed={same(choice, { path: i })}
									onclick={() => choose({ path: i })}>Highlight</button
								>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</details>
{/if}
