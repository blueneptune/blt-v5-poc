# blt UI

blt's web UI: SvelteKit (Svelte 5, TypeScript, Tailwind), served by its
own Node server. Plan and order of work: docs/ui.md.

## Run it

```sh
cd ui
npm ci
cp .env.example .env      # BLT_API_URL, BLT_API_KEY, and the sign-in:
npm run password -- --secret >> .env    # BLT_UI_PASSWORD_HASH, BLT_UI_SESSION_SECRET
npm run dev               # http://localhost:5173 - sign in as admin
```

Any blt API will do: the work desktop's, the lab's, or one run locally
(`uv run uvicorn --factory blt.api.app:create_app --port 8199` with
`BLT_DATABASE_URL` and `BLT_API_KEY` set).

| | |
|---|---|
| `npm run password` | the hash of a sign-in password (`-- --secret` also makes a session secret) |
| `npm run check` | types, including every API call against the spec |
| `npm test` | unit tests (Vitest) |
| `npm run test:pages` | page tests (Playwright): the build, after `npm run build`, against the fake API in `tests/fake-api.ts`; `npx playwright install chromium` once first |
| `npm run api:types` | regenerate `src/lib/api/schema.d.ts` after the API changes (`uv run blt-openapi` first) - CI fails while it is stale |
| `npm run build` | the production server, in `build/` |

Image: `podman build -t blt-ui -f ui/Containerfile ui`; it listens on
3000 and needs `BLT_API_URL`, `BLT_API_KEY`, `BLT_UI_PASSWORD_HASH` and
`BLT_UI_SESSION_SECRET` (and `ORIGIN` behind a proxy) - see
[docs/admin.md](../docs/admin.md).

## How it is put together

- **The browser never calls the API.** Pages load their data on the
  server (`+page.server.ts`) through `locals.api`
  (`src/lib/server/api.ts`), which holds the key. Nothing secret reaches
  the browser.
- **Sign-in is not built, but its places are.** `src/lib/server/auth.ts`
  answers who is asking (today, always the lab user with every role);
  `requireRole` guards a load function, `allowed` filters the navigation
  (`src/lib/nav.ts`, an optional `role` per item). Zitadel replaces
  `currentUser` and the key in `apiFor`; the pages do not change.
- **Filters live in the URL** (`src/lib/jobs.ts`), so every view can be
  linked to and the back button works. The default is backups, last 24 h.
