<script lang="ts">
	import StorageMeter from '$lib/components/StorageMeter.svelte';
	import UsageChart from '$lib/components/UsageChart.svelte';
	import { bytes, when } from '$lib/format';
	import { RANGES, fullIn } from '$lib/storage';

	let { data } = $props();
	const t = $derived(data.target);
	const mountPaths = $derived((t.attributes.mount_paths as { name: string; status: string }[] | undefined) ?? []);
</script>

<a href="/storage" class="text-muted-foreground text-sm hover:underline">← Storage</a>
<h1 class="mt-2 text-2xl font-semibold tracking-tight">{t.name}</h1>
<p class="text-muted-foreground mt-1 text-sm">
	{t.source_name} ({t.source_type}) · {t.kind}{t.product_type ? ` · ${t.product_type}` : ''}
	{#if t.hosts.length}· {t.hosts.join(', ')}{/if}
	{#if !t.present}· <strong>no longer listed</strong> since {when(t.last_seen_at)}{/if}
</p>

<div class="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
	<div class="bg-card rounded-xl border shadow-sm p-4">
		<p class="text-muted-foreground text-xs">Used</p>
		<p class="mt-1 text-2xl font-semibold tabular-nums">{bytes(t.latest?.used_bytes) || '–'}</p>
		<div class="mt-2"><StorageMeter target={t} /></div>
	</div>
	<div class="bg-card rounded-xl border shadow-sm p-4">
		<p class="text-muted-foreground text-xs">Free of capacity</p>
		<p class="mt-1 text-2xl font-semibold tabular-nums">{bytes(t.latest?.free_bytes) || '–'}</p>
		<p class="text-muted-foreground text-xs">of {bytes(t.latest?.capacity_bytes) || 'unknown'}</p>
	</div>
	<div class="bg-card rounded-xl border shadow-sm p-4">
		<p class="text-muted-foreground text-xs">Full in</p>
		<p class="mt-1 text-2xl font-semibold">{fullIn(t) || '–'}</p>
		<p class="text-muted-foreground text-xs">
			{#if t.trend}{bytes(Math.abs(t.trend.used_bytes_per_day))}/day {t.trend.used_bytes_per_day < 0 ? 'freed' : 'more'}, over {t.trend.days} days{:else}needs 6 hours of samples{/if}
		</p>
	</div>
	<div class="bg-card rounded-xl border shadow-sm p-4">
		<p class="text-muted-foreground text-xs">Deduplication</p>
		<p class="mt-1 text-2xl font-semibold tabular-nums">{t.latest?.dedupe_ratio ? `${t.latest.dedupe_ratio}:1` : '–'}</p>
		{#if t.latest?.logical_bytes}<p class="text-muted-foreground text-xs">{bytes(t.latest.logical_bytes)} of data in {bytes(t.latest.physical_bytes)}</p>{/if}
	</div>
</div>

<section class="bg-card mt-6 rounded-xl border shadow-sm p-4">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<h2 class="font-medium">Used over time</h2>
		<div class="flex gap-1 text-sm" role="group" aria-label="Time range">
			{#each Object.keys(RANGES) as range (range)}
				<a
					href="?range={range}"
					aria-current={data.range === range ? 'true' : undefined}
					class="rounded-md px-2 py-1 {data.range === range ? 'bg-accent font-medium' : 'text-muted-foreground hover:bg-accent'}"
					>{range}</a
				>
			{/each}
		</div>
	</div>
	<div class="mt-4"><UsageChart samples={data.samples} /></div>
	<details class="mt-4 text-sm">
		<summary class="text-muted-foreground cursor-pointer">As a table ({data.samples.length} samples)</summary>
		<div class="mt-2 max-h-80 overflow-auto">
			<table class="w-full text-sm">
				<thead class="text-muted-foreground text-left text-xs">
					<tr><th class="py-1">When</th><th class="py-1 text-right">Used</th><th class="py-1 text-right">Free</th><th class="py-1 text-right">Capacity</th></tr>
				</thead>
				<tbody class="striped">
					{#each [...data.samples].reverse() as s (s.collected_at)}
						<tr class="border-t">
							<td class="py-1">{when(s.collected_at)}</td>
							<td class="py-1 text-right tabular-nums">{bytes(s.used_bytes)}</td>
							<td class="py-1 text-right tabular-nums">{bytes(s.free_bytes)}</td>
							<td class="py-1 text-right tabular-nums">{bytes(s.capacity_bytes)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</details>
</section>

{#if mountPaths.length}
	<section class="mt-6">
		<h2 class="text-sm font-medium">Mount paths</h2>
		<ul class="mt-2 text-sm">
			{#each mountPaths as mp (mp.name)}<li>{mp.name} <span class="text-muted-foreground">· {mp.status}</span></li>{/each}
		</ul>
	</section>
{/if}
