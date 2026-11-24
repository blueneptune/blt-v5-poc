<script lang="ts">
	import { TriangleAlert } from '@lucide/svelte';
	import StateBar from '$lib/components/StateBar.svelte';
	import { ago, when } from '$lib/format';
	import { collectionProblem } from '$lib/sources';

	let { data } = $props();
	const sources = $derived(
		data.sources.map((source) => ({ ...source, problem: collectionProblem(source, data.overdueHours) }))
	);
	const late = $derived(sources.filter((source) => source.problem));
</script>

<h1 class="text-2xl font-semibold tracking-tight">Sources</h1>
<p class="text-muted-foreground mt-1 text-sm">
	Every system blt collects from. Adding one here comes with data sources (docs/vision.md 4.2).
</p>

{#if late.length}
	<div
		class="mt-4 flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
		role="alert"
	>
		<TriangleAlert class="mt-0.5 size-4 shrink-0" />
		<ul class="space-y-1">
			{#each late as source (source.id)}
				<li><a href="#source-{source.id}" class="underline-offset-2 hover:underline">{source.problem?.banner}</a></li>
			{/each}
		</ul>
	</div>
{/if}

<div class="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
	{#each sources as source (source.id)}
		<a
			id="source-{source.id}"
			href="/jobs?kind=all&window=7d&source={source.name}"
			class="rounded-xl border p-4 hover:shadow-sm {source.problem
				? 'border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40'
				: 'bg-card'}"
			data-overdue={source.problem ? true : undefined}
		>
			<div class="flex items-baseline justify-between gap-2">
				<h2 class="font-medium">{source.name}</h2>
				<span class="text-muted-foreground text-xs">{source.type}</span>
			</div>
			{#if source.problem}
				<p class="mt-1 flex items-start gap-1 text-xs text-amber-900 dark:text-amber-200">
					<TriangleAlert class="mt-px size-3.5 shrink-0" />
					{source.problem.card}
				</p>
			{:else}
				<p class="text-muted-foreground mt-1 text-xs">
					{source.last_collected_at ? `Last collected ${ago(source.last_collected_at)}` : 'Not collected yet'}
				</p>
			{/if}
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
