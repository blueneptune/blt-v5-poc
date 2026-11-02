<script lang="ts">
	// One horizontal bar split by state: how a group of jobs ended up.
	import { STATES, stateColour } from '$lib/jobs';

	let { states, total, scale = total }: { states: Record<string, number>; total: number; scale?: number } =
		$props();

	const parts = $derived(STATES.filter((s) => states[s]).map((s) => ({ state: s, n: states[s] })));
</script>

<div
	class="bg-muted flex h-2.5 overflow-hidden rounded-full"
	style="width: {scale ? Math.max(4, (100 * total) / scale) : 0}%"
	role="img"
	aria-label={parts.map((p) => `${p.n} ${p.state}`).join(', ')}
>
	{#each parts as part (part.state)}
		<div
			style="width: {(100 * part.n) / total}%; background: {stateColour(part.state)}"
			title="{part.n} {part.state}"
		></div>
	{/each}
</div>
