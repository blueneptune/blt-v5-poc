<script lang="ts">
	import { TriangleAlert } from '@lucide/svelte';
	import StateBar from '$lib/components/StateBar.svelte';
	import { ago, when } from '$lib/format';

	let { data } = $props();
	const overdue = $derived(data.sources.filter((source) => source.overdue));

	function collected(source: { overdue: boolean; last_collected_at?: string | null }): string {
		const last = source.last_collected_at ? `last collected ${ago(source.last_collected_at)}` : 'never collected';
		return source.overdue ? `Overdue: ${last}` : last.charAt(0).toUpperCase() + last.slice(1);
	}
</script>

<h1 class="text-2xl font-semibold tracking-tight">Sources</h1>
<p class="text-muted-foreground mt-1 text-sm">
	Every system blt collects from. Adding one here comes with data sources (docs/vision.md 4.2).
</p>

{#if overdue.length}
	<div
		class="mt-4 flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
		role="alert"
	>
		<TriangleAlert class="mt-0.5 size-4 shrink-0" />
		<p>
			<span class="font-medium">
				{overdue.length === 1 ? '1 source has' : `${overdue.length} sources have`} not been collected in over
				{data.overdueHours} h:
			</span>
			{#each overdue as source, i (source.id)}{i ? ', ' : ' '}<a
					href="#source-{source.id}"
					class="underline-offset-2 hover:underline">{source.name}</a
				>{/each}.
		</p>
	</div>
{/if}

<div class="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
	{#each data.sources as source (source.id)}
		<a
			id="source-{source.id}"
			href="/jobs?kind=all&window=7d&source={source.name}"
			class="rounded-xl border p-4 hover:shadow-sm {source.overdue
				? 'border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40'
				: 'bg-card'}"
			data-overdue={source.overdue || undefined}
		>
			<div class="flex items-baseline justify-between gap-2">
				<h2 class="font-medium">{source.name}</h2>
				<span class="text-muted-foreground text-xs">{source.type}</span>
			</div>
			<p class="mt-1 flex items-center gap-1 text-xs {source.overdue ? 'text-amber-900 dark:text-amber-200' : 'text-muted-foreground'}">
				{#if source.overdue}<TriangleAlert class="size-3.5" />{/if}
				{collected(source)}
			</p>
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
