<script lang="ts">
	/**
	 * A flow run's state and log, kept up to date while it runs: polls
	 * /admin/runs/<id>/log for new lines every two seconds, then once
	 * more after it ends (the last lines can land just after).
	 */
	import { onDestroy, untrack } from 'svelte';
	import RunBadge from './RunBadge.svelte';
	import { isFinal } from '$lib/admin';
	import { duration } from '$lib/format';
	import type { components } from '$lib/api/schema';

	type Run = components['schemas']['FlowRunDetail'];
	type Line = components['schemas']['LogLine'];

	let {
		runId,
		initial = null,
		onupdate,
		height = 'max-h-96'
	}: {
		runId: string;
		initial?: Run | null;
		onupdate?: (run: Run, lines: Line[]) => void;
		height?: string;
	} = $props();

	let run = $state<Run | null>(null);
	let lines = $state<Line[]>([]);
	let problem = $state<string | null>(null);
	let box = $state<HTMLElement | null>(null);
	let timer: ReturnType<typeof setTimeout> | undefined;
	let stopped = false;
	let lastPolls = 0;

	async function poll() {
		try {
			const response = await fetch(`/admin/runs/${runId}/log?offset=${lines.length}`);
			if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
			const next: Run = await response.json();
			const atBottom = box ? box.scrollHeight - box.scrollTop - box.clientHeight < 24 : true;
			run = next;
			if (next.logs.length) lines = [...lines, ...next.logs];
			problem = null;
			onupdate?.(next, lines);
			if (atBottom) queueMicrotask(() => box && (box.scrollTop = box.scrollHeight));
		} catch (e) {
			problem = e instanceof Error ? e.message : String(e);
		}
		if (stopped) return;
		if (!run || !isFinal(run.state_type)) timer = setTimeout(poll, 2000);
		else if (lastPolls++ < 2) timer = setTimeout(poll, 1500);
	}

	// Starts again for each run id; nothing else re-runs it.
	$effect(() => {
		void runId;
		untrack(() => {
			clearTimeout(timer);
			lastPolls = 0;
			run = initial;
			lines = initial ? [...initial.logs] : [];
			if (initial) onupdate?.(initial, lines);
			poll();
		});
		return () => clearTimeout(timer);
	});

	onDestroy(() => {
		stopped = true;
		clearTimeout(timer);
	});

	const LEVEL: Record<string, string> = {
		WARNING: 'text-amber-700 dark:text-amber-300',
		ERROR: 'text-red-700 dark:text-red-300',
		CRITICAL: 'text-red-700 dark:text-red-300'
	};
	const time = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
</script>

<div class="bg-card rounded-xl border shadow-sm">
	<div class="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-sm">
		{#if run}
			<RunBadge type={run.state_type} name={run.state_name} />
			<a href="/admin/runs/{run.id}" class="font-medium hover:underline">{run.name}</a>
			<span class="text-muted-foreground">{run.flow_name}</span>
			{#if run.start_time}
				<span class="text-muted-foreground ml-auto tabular-nums">
					{duration(run.start_time, run.end_time)}
				</span>
			{/if}
		{:else}
			<span class="text-muted-foreground">Starting…</span>
		{/if}
	</div>
	<div bind:this={box} class="{height} overflow-auto p-3 font-mono text-xs leading-relaxed" aria-live="polite">
		{#each lines as line, i (i)}
			<div class="flex gap-3 {LEVEL[line.level] ?? ''}">
				<span class="text-muted-foreground shrink-0 tabular-nums">{time.format(new Date(line.timestamp))}</span>
				<span class="break-all whitespace-pre-wrap">{line.message}</span>
			</div>
		{:else}
			<p class="text-muted-foreground">
				{run && !isFinal(run.state_type) ? 'Waiting for a worker to pick it up…' : 'No log lines.'}
			</p>
		{/each}
		{#if problem}
			<p class="text-destructive mt-2">Could not read the run: {problem}</p>
		{/if}
	</div>
	{#if run?.message && isFinal(run.state_type) && run.state_type !== 'COMPLETED'}
		<p class="border-t px-3 py-2 text-sm">{run.message}</p>
	{/if}
</div>
