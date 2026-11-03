# The web UI

**Status: started 2026-11-02.** The shell, the overview, the jobs page,
a page per job and the sources page are built (`ui/`), against
`/v1/jobs` and `/v1/sources`; not yet deployed. How to run it:
[ui/README.md](../ui/README.md). What it is for: [vision.md](vision.md)
section 4.

## Decisions (2026-11-02)

| | |
|---|---|
| Where | `ui/` in this repository: the front end of the blt platform, released with the API it reads. Its API types are generated from `docs/openapi.json` and CI fails when they drift. |
| Framework | SvelteKit 2 with Svelte 5, TypeScript, Tailwind 4, its Node server (adapter-node) - continuing what bartman_v2 started. |
| Talking to the API | Only from the UI's server side. The browser never sees the API key or, later, a user's token. |
| Sign-in | **Not yet - the places are built, the locks are not fitted** (the owner: "build the spots where the padlocks can be added but don't add them yet"). One function says who is asking (today always the lab user, every role); every page and menu item already asks it. Zitadel comes nearer a true proof of concept. |
| Filters | In the URL, so views are links. Default: backups, last 24 h (common-jobs decision 3). On wider screens the filter bar stays pinned and the table scrolls under it, its column headings kept at its top; the pin button lets it all scroll away, remembered per browser. |

## Libraries

In use: `openapi-typescript` + `openapi-fetch` (typed API calls),
`@lucide/svelte` (icons), `mode-watcher` (light/dark), Vitest, Playwright
(screenshots so far; page tests to come). The card and button components
are bartman_v2's shadcn-style ones.

Planned, each when a page needs it - not before:

| Need | Choice |
|---|---|
| Column chooser, per-product columns, sorting on the jobs page | TanStack Table (table-core), with TanStack Virtual for long lists |
| Forms: adding a data source | sveltekit-superforms + zod |
| Charts beyond the state bars | LayerChart |
| Menus, dialogs, comboboxes | bits-ui (shadcn-svelte) |

## Order of work

1. ~~Shell, overview, jobs, job page, sources~~ - done 2026-11-02.
2. ~~**In the lab**~~ - done in 2026.11.02: https://ui.blt.lab.ethos.home
   (`blt-ui` beside `blt-api`, step-ca certificate). **On the desktop**
   (deploy/up.sh) from 2026.11.02.1, on localhost only - see
   production-setup.md, "The web UI".
3. **Jobs page, richer**: column chooser and product-specific columns
   (vision 4.4), sorting, the inventory row a job ran on, a "last run"
   per source on the overview (collection health).
4. **Page tests** with Playwright against an API with known data, in CI.
5. **Sign-in** (Zitadel), roles, saved per-user filters, role-scoped kinds
   - nearer a true proof of concept.
6. Data sources and work pools (vision 4.2, 4.3).
