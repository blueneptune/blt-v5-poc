<script lang="ts">
	// How full one target is: a meter whose fill carries its health, with
	// an icon and a word beside it once it needs attention - the colour
	// never says it alone.
	import { CircleAlert, TriangleAlert } from '@lucide/svelte';
	import { HEALTH_LABEL, health, healthColour, usedFraction, type Target } from '$lib/storage';

	let { target }: { target: Target } = $props();

	const used = $derived(usedFraction(target));
	const state = $derived(health(target));
</script>

<div class="flex items-center gap-2">
	<div
		class="h-2 min-w-16 flex-1 overflow-hidden rounded-full"
		style="background: var(--meter-track)"
		role="meter"
		aria-valuemin={0}
		aria-valuemax={100}
		aria-valuenow={used === null ? undefined : Math.round(used * 100)}
		aria-label="{used === null ? 'capacity unknown' : `${Math.round(used * 100)}% used`}, {HEALTH_LABEL[state]}"
	>
		{#if used !== null}
			<div
				class="h-full rounded-full"
				style="width: {Math.max(used * 100, 1)}%; background: {healthColour(state)}"
			></div>
		{/if}
	</div>
	<span class="w-10 text-right text-sm tabular-nums">{used === null ? '–' : `${Math.round(used * 100)}%`}</span>
	{#if state === 'critical'}
		<span class="flex items-center gap-1 text-xs font-medium whitespace-nowrap">
			<CircleAlert class="size-3.5" style="color: {healthColour(state)}" />{HEALTH_LABEL[state]}
		</span>
	{:else if state === 'warning'}
		<span class="flex items-center gap-1 text-xs font-medium whitespace-nowrap">
			<TriangleAlert class="size-3.5" style="color: {healthColour(state)}" />{HEALTH_LABEL[state]}
		</span>
	{/if}
</div>
