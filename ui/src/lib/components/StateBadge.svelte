<script lang="ts">
	import { stateClass } from '$lib/jobs';

	let { state, title }: { state: string; title?: string } = $props();
</script>

<span
	class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium {stateClass(state)}"
	{title}
>
	{state}
</span>
