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
	import { CircleAlert, TriangleAlert } from '@lucide/svelte';
	import { bytes } from '$lib/format';
	import { KIND_COLOUR, KIND_LABEL, SIZE, columnsOf, layout, type Band, type Box, type FlowKind, type Flows } from '$lib/sankey';
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
		<li class="text-muted-foreground">Pools: the bar fills as the pool does.</li>
	</ul>

	<div class="relative mt-3 overflow-x-auto">
		<svg
			viewBox="0 0 {SIZE.width} {H}"
			class="block w-full min-w-[720px]"
			role="img"
			aria-label="{three
				? `${flows.primary}'s copies: from ${inColumn(0)} pools through ${inColumn(1)} SLPs into ${inColumn(2)} pools`
				: `Where NetBackup's data went: ${flows.links.length} flows into ${inColumn(1)} pools`}. The table below has the same figures."
			onmouseleave={() => {
				hover = null;
				active = null;
			}}
		>
			<g transform="translate(0 {TOP})">
				{#each drawn.bands as band (band.link.source + band.link.target + band.link.kind)}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<path
						d={band.path}
						fill={KIND_COLOUR[band.link.kind]}
						fill-opacity={active === null ? 0.4 : active === band ? 0.75 : 0.15}
						stroke="var(--card)"
						stroke-width="1"
						onmousemove={(e) => {
							active = band;
							at(e, bandLines(band));
						}}
					/>
				{/each}

				{#each drawn.boxes as box (box.node.id)}
					{@const t = target(box)}
					{@const used = t ? usedFraction(t) : null}
					{@const state = t ? health(t) : 'unknown'}
					<!-- svelte-ignore a11y_no_static_element_interactions -->
					<g
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
						<th class="px-3 py-2 font-medium">To</th>
						<th class="px-3 py-2 font-medium">How</th>
						<th class="px-3 py-2 text-right font-medium">Data</th>
						<th class="px-3 py-2 text-right font-medium">Jobs</th>
					</tr>
				</thead>
				<tbody class="striped">
					{#each [...flows.links].sort((a, b) => b.bytes - a.bytes) as link (link.source + link.target + link.kind)}
						<tr class="border-t">
							<td class="px-3 py-2">{names.get(link.source) ?? link.source}</td>
							<td class="px-3 py-2">{names.get(link.target) ?? link.target}</td>
							<td class="px-3 py-2">{KIND_LABEL[link.kind]}</td>
							<td class="px-3 py-2 text-right tabular-nums">{bytes(link.bytes)}</td>
							<td class="px-3 py-2 text-right tabular-nums">{link.jobs}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</details>
{/if}
