# The Commvault SDK

The code that talks to Commvault is not part of blt. It is its own
package, **commvault-sdk**, in its own repository
(`yoki-py-commvault-sdk`), and blt depends on it
the way it depends on `sdk-primer-core`.

- What it does, every call with its endpoint, and how far each has been
  proven against a real CommServe: that repository's README.
- Adding or changing a Commvault call: do it there, release a tag, and
  bump the tag here.

## How blt uses it

```
commvault-sdk            CommvaultClient: cv.jobs, cv.clients, cv.sql, ...
   ^                     returns its own typed Job, and Commvault's JSON
   |
src/blt/collector/
   connect.py            builds a CommvaultClient from a CommCell's settings
   source.py             the one place an SDK Job becomes blt's JobIn
   inventory.py          clients / instances / databases -> blt's ObjectIn
   tokens.py             writes renewed tokens back to the .env file
   probe.py, lookup.py   --probe and blt-lookup
lab/cvlab.py             the only caller that asks for allow_changes=True
```

Only the collector and the lab need the SDK. The API container does not
install it: `blt.schemas` holds blt's own wire models precisely so that
the API never has to.

## Where it comes from

The repository is
[`yoki-py-commvault-sdk`](https://github.com/blueneptune/yoki-py-commvault-sdk);
the package it provides is `commvault-sdk`, imported as `commvault_sdk`.
blt pins a tag of it in `pyproject.toml`, under `[tool.uv.sources]`:

```
commvault-sdk = { git = "https://github.com/blueneptune/yoki-py-commvault-sdk.git", tag = "v0.1.0" }
```

`uv` fetches it; nothing needs cloning by hand. To take a newer SDK,
change the tag and run `uv lock`.

To change the SDK and blt together, clone it next to this repository and
point the source at that checkout for the duration:

```
commvault-sdk = { path = "../yoki-py-commvault-sdk", editable = true }
```
