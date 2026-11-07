<script lang="ts">
	import { page } from '$app/state';
	import { NAV, isActive } from '$lib/nav';

	let { children } = $props();
	const tabs = NAV.find((item) => item.href === '/admin')?.children ?? [];
</script>

<!-- On a phone the side panel has one Admin link; these are its pages. -->
<nav aria-label="Administration" class="mb-4 flex gap-1 overflow-x-auto border-b md:hidden">
	{#each tabs as tab (tab.href)}
		{@const on = isActive(page.url.pathname, tab.href)}
		<a
			href={tab.href}
			aria-current={on ? 'page' : undefined}
			class="-mb-px border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap {on
				? 'border-foreground text-foreground'
				: 'text-muted-foreground hover:text-foreground border-transparent'}">{tab.label}</a
		>
	{/each}
</nav>
{@render children()}
