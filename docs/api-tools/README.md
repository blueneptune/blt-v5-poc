# blt's API in Bruno, Postman and Insomnia

Ready-made imports of every endpoint, in folders by the API's tags, with
a **Common tasks** folder of requests ready to send: failed jobs of the
last day, jobs still running, counts per source, storage, sources and
their next starts, run health, checking a source, turning collection on.

| Tool | Import |
|---|---|
| Bruno | Open Collection -> `bruno/` (the folder) |
| Postman | Import -> `postman/blt.postman_collection.json` and the `postman/blt-*.postman_environment.json` you need |
| Insomnia | Import -> `insomnia/blt.insomnia.json` (its environments come with it) |

Each install is an environment - `desktop` (http://127.0.0.1:8088),
`lab`, `customer` (a placeholder to copy) - with `apiKey` left **empty**.
Put the key in the tool's environment (Postman: as a secret; Bruno keeps
it out of its files as `vars:secret`), never in a file here. Other
variables: `since` (an ISO time, for the Common tasks), `sourceType` and
`sourceName` (for checking one source), and each path's own (`job_id`,
`source_name`, ...).

These files are made by `uv run blt-api-collections` from
`docs/openapi.json`; don't edit them by hand. A test fails when the API
changed and they were not regenerated - run `uv run blt-openapi` then
`uv run blt-api-collections`.
