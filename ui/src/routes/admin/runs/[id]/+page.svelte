<script lang="ts">
	import { enhance } from '$app/forms';
	import { Button } from '$lib/components/ui/button';
	import RunLog from '$lib/components/RunLog.svelte';
	import { isFinal } from '$lib/admin';
	import { when } from '$lib/format';

	let { data, form } = $props();
	let latest = $state<typeof data.run | null>(null);
	const live = $derived(latest ?? data.run);
</script>

<svelte:head><title>{data.run.name} · Runs · blt</title></svelte:head>

<a href="/admin/runs" class="text-muted-foreground text-sm hover:underline">← Runs</a>
<div class="mt-2 flex flex-wrap items-center gap-3">
	<h1 class="text-2xl font-semibold tracking-tight">{data.run.name}</h1>
	<span class="text-muted-foreground text-sm">{data.run.flow_name}{data.run.deployment_name ? ` / ${data.run.deployment_name}` : ''}</span>
	{#if !isFinal(live.state_type) && live.state_type !== 'CANCELLING'}
		<form method="POST" action="?/cancel" use:enhance class="ml-auto">
			<Button type="submit" size="sm" variant="outline">Cancel the run</Button>
		</form>
	{/if}
</div>

{#if form?.message}<p class="text-destructive mt-3 text-sm" role="alert">{form.message}</p>{/if}

<dl class="mt-4 grid max-w-2xl grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
	<dt class="text-muted-foreground">Due</dt>
	<dd>{when(live.expected_start_time)}</dd>
	<dt class="text-muted-foreground">Started</dt>
	<dd>{when(live.start_time) || '—'}</dd>
	<dt class="text-muted-foreground">Ended</dt>
	<dd>{when(live.end_time) || '—'}</dd>
	<dt class="text-muted-foreground">Parameters</dt>
	<dd class="font-mono text-xs break-all">{JSON.stringify(live.parameters)}</dd>
</dl>

<div class="mt-6">
	<RunLog runId={data.run.id} initial={data.run} height="max-h-[65svh]" onupdate={(run) => (latest = run)} />
</div>
{#if data.run.logs_truncated}
	<p class="text-muted-foreground mt-2 text-xs">Only the first 2000 lines are shown.</p>
{/if}
