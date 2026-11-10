<script lang="ts">
	import StorageMeter from '$lib/components/StorageMeter.svelte';
	import { bytes, ago } from '$lib/format';
	import { GROUPS, GROUP_LABEL, fullIn, grouped, totals } from '$lib/storage';

	let { data } = $props();

	const sum = $derived(totals(data.targets));
	const groups = $derived(grouped(data.targets, data.by));
	const perDay = (n: number | undefined) =>
		n === undefined ? '' : `${n < 0 ? '−' : '+'}${bytes(Math.abs(n))}/day`;
</script>

<h1 class="text-2xl font-semibold tracking-tight">Storage</h1>
<p class="text-muted-foreground mt-1 text-sm">
	Where each source writes its backups, and how full it is. Collected every hour.
</p>

<div class="mt-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
	<div class="bg-card rounded-xl border shadow-sm p-4">
		<p class="text-muted-foreground text-xs">Capacity</p>
		<p class="mt-1 text-2xl font-semibold tabular-nums">{bytes(sum.capacity) || '0 B'}</p>
	</div>
	<div class="bg-card rounded-xl border shadow-sm p-4">
		<p class="text-muted-foreground text-xs">Used</p>
		<p class="mt-1 text-2xl font-semibold tabular-nums">{bytes(sum.used) || '0 B'}</p>
	</div>
	<div class="bg-card rounded-xl border shadow-sm p-4">
		<p class="text-muted-foreground text-xs">Free</p>
		<p class="mt-1 text-2xl font-semibold tabular-nums">{bytes(sum.free) || '0 B'}</p>
	</div>
	<div class="bg-card rounded-xl border shadow-sm p-4">
		<p class="text-muted-foreground text-xs">Need attention</p>
		<p class="mt-1 text-2xl font-semibold tabular-nums">{sum.attention}</p>
		<p class="text-muted-foreground text-xs">of {data.targets.length} targets</p>
	</div>
</div>

<div class="mt-6 flex flex-wrap items-center gap-2 text-sm" role="group" aria-label="Group by">
	<span class="text-muted-foreground">Group by</span>
	{#each GROUPS as group (group)}
		<a
			href="?by={group}"
			aria-current={data.by === group ? 'true' : undefined}
			class="rounded-full border px-3 py-1 {data.by === group
				? 'bg-accent text-accent-foreground font-medium'
				: 'text-muted-foreground hover:bg-accent'}">{GROUP_LABEL[group]}</a
		>
	{/each}
</div>

{#each groups as group (group.name)}
	<section class="mt-6">
		<h2 class="text-sm font-medium">{group.name}</h2>
		<div class="bg-card mt-2 overflow-x-auto rounded-xl border shadow-sm">
			<table class="w-full min-w-[720px] text-sm">
				<thead class="bg-muted/60 text-muted-foreground text-left text-xs">
					<tr>
						<th class="px-3 py-2 font-medium">Name</th>
						<th class="px-3 py-2 font-medium">Kind</th>
						<th class="w-64 px-3 py-2 font-medium">Used</th>
						<th class="px-3 py-2 text-right font-medium">Used / capacity</th>
						<th class="px-3 py-2 text-right font-medium">Growth</th>
						<th class="px-3 py-2 text-right font-medium">Full in</th>
					</tr>
				</thead>
				<tbody class="striped">
					{#each group.targets as target (target.id)}
						<tr class="border-t">
							<td class="px-3 py-2">
								<a href="/storage/{target.id}" class="font-medium hover:underline">{target.name}</a>
								<div class="text-muted-foreground text-xs">
									{data.by === 'source' ? target.hosts.join(', ') : target.source_name}
									{#if target.latest}· {ago(target.latest.collected_at)}{/if}
								</div>
							</td>
							<td class="px-3 py-2">
								{target.kind}
								{#if target.product_type}<div class="text-muted-foreground text-xs">{target.product_type}</div>{/if}
							</td>
							<td class="px-3 py-2"><StorageMeter {target} /></td>
							<td class="px-3 py-2 text-right whitespace-nowrap tabular-nums">
								{bytes(target.latest?.used_bytes)}
								{#if target.latest?.capacity_bytes}<span class="text-muted-foreground"> / {bytes(target.latest.capacity_bytes)}</span>{/if}
							</td>
							<td class="px-3 py-2 text-right whitespace-nowrap tabular-nums">{perDay(target.trend?.used_bytes_per_day)}</td>
							<td class="px-3 py-2 text-right whitespace-nowrap">{fullIn(target)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</section>
{:else}
	<p class="text-muted-foreground mt-6 text-sm">No storage collected yet.</p>
{/each}
