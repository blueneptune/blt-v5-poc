<script lang="ts">
	import { cn } from '$lib/utils.js';
	import type { Snippet } from 'svelte';
	import type { HTMLAttributes } from 'svelte/elements';

	let {
		class: className,
		children,
		...restProps
	}: HTMLAttributes<HTMLDivElement> & { children?: Snippet } = $props();
</script>

<div
	class={cn('bg-card text-card-foreground flex flex-col gap-6 rounded-xl border shadow-sm py-6', className)}
	{...restProps}
>
	{@render children?.()}
</div>
