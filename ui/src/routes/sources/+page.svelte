<script lang="ts">
	import StateBar from '$lib/components/StateBar.svelte';
	import { when } from '$lib/format';

	let { data } = $props();
</script>

<h1 class="text-2xl font-semibold tracking-tight">Sources</h1>
<p class="text-muted-foreground mt-1 text-sm">
	Every system blt collects from. Adding one here comes with data sources (docs/vision.md 4.2).
</p>

<div class="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
	{#each data.sources as source (source.id)}
		<a href="/jobs?kind=all&window=7d&source={source.name}" class="bg-card rounded-xl border p-4 hover:shadow-sm">
			<div class="flex items-baseline justify-between gap-2">
				<h2 class="font-medium">{source.name}</h2>
				<span class="text-muted-foreground text-xs">{source.type}</span>
			</div>
			<p class="text-muted-foreground mt-3 text-xs">Jobs, last 7 days</p>
			{#if source.week}
				<div class="mt-1 flex items-center gap-2">
					<div class="flex-1"><StateBar states={source.week.states} total={source.week.total} /></div>
					<span class="text-sm tabular-nums">{source.week.total}</span>
				</div>
			{:else}
				<p class="mt-1 text-sm">none</p>
			{/if}
			<p class="text-muted-foreground mt-3 text-xs">Collected since {when(source.created_at)}</p>
		</a>
	{:else}
		<p class="text-muted-foreground text-sm">Nothing collected yet.</p>
	{/each}
</div>
