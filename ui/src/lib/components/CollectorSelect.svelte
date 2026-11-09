<script lang="ts">
	import { collectorChoices, type Collector } from '$lib/admin';

	/** Which collector reaches a source: blt's own worker ('') or a remote
	 * one (docs/remote-collector.md). With `submit`, choosing one submits
	 * its form at once. */
	let {
		collectors,
		value = null,
		id,
		label,
		submit = false
	}: {
		collectors: Collector[];
		value?: string | null;
		id?: string;
		label?: string;
		submit?: boolean;
	} = $props();
</script>

<select
	{id}
	name="collector"
	aria-label={label}
	class="bg-background h-8 rounded-md border px-2 text-sm"
	onchange={(e) => submit && e.currentTarget.form?.requestSubmit()}
>
	{#each collectorChoices(collectors, value) as choice (choice.value)}
		<option value={choice.value} selected={choice.value === (value ?? '')}>{choice.label}</option>
	{/each}
</select>
