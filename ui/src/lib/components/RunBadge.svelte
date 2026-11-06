<script lang="ts">
	import { runTone } from '$lib/admin';
	import { stateClass } from '$lib/jobs';

	let { type, name }: { type: string | null | undefined; name?: string | null } = $props();
</script>

<span
	class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium {stateClass(runTone(type))}"
>
	{name ?? type ?? 'unknown'}
</span>
