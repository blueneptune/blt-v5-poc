<script lang="ts">
	// Used space over time, one series, with capacity as a dashed
	// reference line on the same axis. A crosshair snaps to the nearest
	// sample and reads out both values.
	import { bytes, when } from '$lib/format';
	import { points, type Sample } from '$lib/storage';

	let { samples }: { samples: Sample[] } = $props();

	/** A round step in the binary unit the values are in: 1, 2, 2.5, 5 or 10 GiB, TiB, ... */
	function niceStep(raw: number): number {
		const unit = 1024 ** Math.max(0, Math.floor(Math.log(raw) / Math.log(1024)));
		const v = raw / unit;
		const magnitude = 10 ** Math.floor(Math.log10(v));
		const step = [1, 2, 2.5, 5, 10].find((m) => m * magnitude >= v)! * magnitude;
		return step * unit;
	}

	const W = 720;
	const H = 240;
	const PAD = { top: 12, right: 12, bottom: 24, left: 64 };

	const used = $derived(points(samples, 'used_bytes'));
	const capacity = $derived(points(samples, 'capacity_bytes'));
	const xs = $derived(used.map((p) => p.x));
	const x0 = $derived(Math.min(...xs));
	const x1 = $derived(Math.max(...xs));
	const yStep = $derived(niceStep(Math.max(1, ...used.map((p) => p.y), ...capacity.map((p) => p.y)) / 4));
	const yMax = $derived(Math.ceil(Math.max(1, ...used.map((p) => p.y), ...capacity.map((p) => p.y)) * 1.02 / yStep) * yStep);

	const sx = (x: number) => PAD.left + ((x - x0) / Math.max(1, x1 - x0)) * (W - PAD.left - PAD.right);
	const sy = (y: number) => PAD.top + (1 - y / yMax) * (H - PAD.top - PAD.bottom);
	const path = (ps: { x: number; y: number }[]) =>
		ps.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join('');

	const ticks = $derived(Array.from({ length: Math.round(yMax / yStep) + 1 }, (_, i) => i * yStep));
	const dayTicks = $derived.by(() => {
		if (used.length < 2) return [];
		const n = 4;
		return Array.from({ length: n + 1 }, (_, i) => x0 + ((x1 - x0) * i) / n);
	});
	const short = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });

	let hover = $state<number | null>(null);
	function move(event: PointerEvent) {
		const svg = event.currentTarget as SVGSVGElement;
		const box = svg.getBoundingClientRect();
		const x = ((event.clientX - box.left) / box.width) * W;
		let best = 0;
		for (let i = 1; i < used.length; i++)
			if (Math.abs(sx(used[i].x) - x) < Math.abs(sx(used[best].x) - x)) best = i;
		hover = used.length ? best : null;
	}
	const at = $derived(hover === null ? null : used[hover]);
	const capAt = $derived(at ? capacity.find((c) => c.x === at.x) : undefined);
</script>

{#if used.length < 2}
	<p class="text-muted-foreground py-8 text-center text-sm">Not enough samples yet for a chart.</p>
{:else}
	<div class="relative">
		<svg
			viewBox="0 0 {W} {H}"
			class="w-full touch-none select-none"
			role="img"
			aria-label="Used space over time"
			onpointermove={move}
			onpointerleave={() => (hover = null)}
		>
			{#each ticks as t (t)}
				<line x1={PAD.left} x2={W - PAD.right} y1={sy(t)} y2={sy(t)} class="stroke-border" stroke-width="1" />
				<text x={PAD.left - 6} y={sy(t) + 4} text-anchor="end" class="fill-muted-foreground text-[10px]">{bytes(t)}</text>
			{/each}
			{#each dayTicks as d (d)}
				<text x={sx(d)} y={H - 6} text-anchor="middle" class="fill-muted-foreground text-[10px]">{short.format(d)}</text>
			{/each}
			<path
				d="{path(used)}L{sx(x1)},{sy(0)}L{sx(x0)},{sy(0)}Z"
				style="fill: var(--chart-area)"
			/>
			{#if capacity.length}
				<path d={path(capacity)} fill="none" stroke-dasharray="4 4" stroke-width="1.5" style="stroke: var(--chart-reference)" />
			{/if}
			<path d={path(used)} fill="none" stroke-width="2" stroke-linejoin="round" style="stroke: var(--chart-line)" />
			{#if at}
				<line x1={sx(at.x)} x2={sx(at.x)} y1={PAD.top} y2={H - PAD.bottom} class="stroke-muted-foreground" stroke-width="1" />
				<circle cx={sx(at.x)} cy={sy(at.y)} r="4" class="stroke-card" stroke-width="2" style="fill: var(--chart-line)" />
			{/if}
		</svg>
		{#if at}
			<div
				class="bg-card text-card-foreground pointer-events-none absolute top-2 rounded-md border px-2 py-1 text-xs shadow-sm"
				style="left: clamp(0px, calc({(sx(at.x) / W) * 100}% - 70px), calc(100% - 150px))"
			>
				<div class="text-muted-foreground">{when(new Date(at.x).toISOString())}</div>
				<div><strong class="tabular-nums">{bytes(at.y)}</strong> <span class="text-muted-foreground">used</span></div>
				{#if capAt}
					<div><strong class="tabular-nums">{bytes(capAt.y)}</strong> <span class="text-muted-foreground">capacity</span></div>
				{/if}
			</div>
		{/if}
	</div>
	<div class="text-muted-foreground mt-2 flex gap-4 text-xs">
		<span class="flex items-center gap-1.5"><span class="inline-block h-0.5 w-4" style="background: var(--chart-line)"></span>Used</span>
		<span class="flex items-center gap-1.5"><span class="inline-block w-4 border-t-2 border-dashed" style="border-color: var(--chart-reference)"></span>Capacity</span>
	</div>
{/if}
