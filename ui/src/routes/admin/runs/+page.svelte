<script lang="ts">
	import RunBadge from '$lib/components/RunBadge.svelte';
	import { duration, when } from '$lib/format';
	import { page as current } from '$app/state';

	let { data } = $props();
	const FILTERS = ['all', 'running', 'failed', 'completed', 'scheduled'] as const;

	function link(changes: Record<string, string | null>): string {
		const params = new URLSearchParams(current.url.searchParams);
		for (const [key, value] of Object.entries(changes)) {
			if (value === null || value === '' || (key === 'state' && value === 'all')) params.delete(key);
			else params.set(key, value);
		}
		if (!('page' in changes)) params.delete('page');
		const query = params.toString();
		return query ? `?${query}` : '?';
	}
	const chip = (on: boolean) =>
		`rounded-full border px-3 py-1 text-xs font-medium ${on ? 'bg-accent text-accent-foreground' : 'text-muted-foreground hover:bg-accent'}`;
</script>

<svelte:head><title>Runs · Administration · blt</title></svelte:head>

<h1 class="text-2xl font-semibold tracking-tight">Runs</h1>
<p class="text-muted-foreground mt-1 text-sm">
	Every flow run, newest first{#if data.deployment}, of <strong>{data.deployment.flow_name}/{data.deployment.name}</strong>
		<a href={link({ deployment: null })} class="ml-1 underline">all flows</a>{/if}.
</p>

<div class="mt-4 flex flex-wrap gap-2">
	{#each FILTERS as f (f)}
		<a href={link({ state: f })} class={chip(data.filter === f)}>{f}</a>
	{/each}
	<a href={current.url.pathname + current.url.search} class="text-muted-foreground ml-auto text-xs underline" data-sveltekit-reload>
		Refresh
	</a>
</div>

{#if data.problem}
	<p class="text-destructive mt-6 text-sm" role="alert">Could not read the runs: {data.problem}</p>
{/if}

<div class="bg-card mt-4 overflow-x-auto rounded-xl border shadow-sm">
	<table class="w-full text-sm">
		<thead class="text-muted-foreground border-b text-left text-xs">
			<tr>
				<th class="px-4 py-2 font-medium">State</th>
				<th class="px-4 py-2 font-medium">Run</th>
				<th class="px-4 py-2 font-medium">Flow</th>
				<th class="px-4 py-2 font-medium">Started</th>
				<th class="px-4 py-2 font-medium">Took</th>
				<th class="px-4 py-2 font-medium">By</th>
			</tr>
		</thead>
		<tbody class="striped">
			{#each data.runs as run (run.id)}
				<tr class="border-b last:border-0">
					<td class="px-4 py-2"><RunBadge type={run.state_type} name={run.state_name} /></td>
					<td class="px-4 py-2"><a href="/admin/runs/{run.id}" class="font-medium hover:underline">{run.name}</a></td>
					<td class="text-muted-foreground px-4 py-2">{run.flow_name}{run.deployment_name ? ` / ${run.deployment_name}` : ''}</td>
					<td class="px-4 py-2 whitespace-nowrap">{when(run.start_time ?? run.expected_start_time)}</td>
					<td class="px-4 py-2 tabular-nums">{duration(run.start_time, run.end_time)}</td>
					<td class="text-muted-foreground px-4 py-2">{run.auto_scheduled ? 'schedule' : 'hand or flow'}</td>
				</tr>
			{:else}
				<tr><td colspan="6" class="text-muted-foreground px-4 py-6 text-center">No runs.</td></tr>
			{/each}
		</tbody>
	</table>
</div>

<div class="mt-4 flex gap-4 text-sm">
	{#if data.page > 0}<a href={link({ page: String(data.page - 1) })} class="underline">Newer</a>{/if}
	{#if data.runs.length === 50}<a href={link({ page: String(data.page + 1) })} class="underline">Older</a>{/if}
</div>
