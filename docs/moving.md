# Moving blt to a tools server

`deploy/move.sh` moves a running blt, with its data, from the machine it
runs on now (WSL, say) to another one: a tools server that stays up. Run
it on the machine blt runs on now, from a release tag:

```bash
git fetch --tags && git checkout 2026.11.11      # the release to move
deploy/move.sh tools-us --adopt-collector us     # the move
./deploy/down.sh                                 # stop the copy here
```

`tools-us` is anything ssh takes (`user@host`, or a `Host` in
`~/.ssh/config`). `--adopt-collector us` is only for a remote collector
already on that server (docs/remote-collector.md); leave it out otherwise.

## What it does

1. **Checks the server** before changing anything. It needs podman. It
   stops if blt's data is already there (the `blt-pgdata` volume), so a
   second run can never land on a live database. It also stops if this
   checkout has uncommitted changes.
2. **Copies the code** of this checkout (an archive, so the server needs
   no git and no internet) into `~/blt`, and **`config/blt.env`** through
   ssh's input, never on a command line. The UI settings for the server
   are added to the end (see below). An existing `config/blt.env` there
   is kept.
3. **Builds the images from this checkout** (only the build; podman's
   cache makes it quick when nothing changed) and **copies them**: the
   API, UI, Prefect server and worker, and Postgres, each only if the
   server has a different one.
4. **Dumps both databases here**, `blt` and `prefect`, and checks that
   each reads back.
5. **Starts blt there empty**, which makes the databases. Then it stops
   everything but Postgres, **restores both dumps**, and starts blt again.
   The `prefect` database holds sources, schedules and their secrets, and
   the key that decrypts them, so everything arrives working.
6. With `--adopt-collector NAME`, it points that collector at the Prefect
   and API now on the same server, instead of at the tunnel. Sources
   already set to that collector need no change.

This machine's blt keeps running until you stop it. Nothing collected in
between is lost: the server's next run collects everything since the last
run in the dump.

## The UI's port

By default the UI listens on **443** on every address of the server, as
plain HTTP for now: `http://<server>:443`. The browser address is the
server's full name, as `hostname -f` reports it there. You can change
any of these:

| Option | Default |
|---|---|
| `--ui-port PORT` | `443` |
| `--ui-address IP` | `0.0.0.0` |
| `--ui-origin URL` | `http://<hostname -f>:PORT`, the address people type, which the UI checks sign-in posts against |
| `--dir DIR` | `blt`, under the home folder |

Rootless podman may not listen below port 1024 by default. If the server
does not allow 443, move.sh stops and prints the one setting someone with
root adds there once:

```bash
echo 'net.ipv4.ip_unprivileged_port_start=443' | sudo tee /etc/sysctl.d/90-blt.conf
sudo sysctl --system
```

The server's firewall must also let 443 in, which is the reason for that
port.

The API (8088), Prefect (4200) and Postgres stay on the server's loopback,
as they did in WSL. To let people on the network use the API and its
reference (`http://<server>:8088/docs`), add `BLT_API_BIND_ADDRESS=0.0.0.0`
to `config/blt.env` there and run `./deploy/down.sh` then `./deploy/up.sh`
(the data is kept). Postgres stays local. The firewall must let 8088 in. A remote collector elsewhere reaches them through a
tunnel from this server, `deploy/collector.sh tunnel` run there.

**Plain HTTP:** the sign-in password crosses the network unencrypted. A
certificate and HTTPS are the next step once the server has a proper name.

## After the move, on the server

```bash
cd ~/blt
./deploy/backup.sh install-timer     # nightly backups there
loginctl enable-linger $USER         # blt keeps running after you log out
```

Then, back on the old machine, `deploy/collector.sh drop-tunnel tools-us`
if you had a tunnel kept up there.

## Later releases

The server has no git or build tools to rely on, so releases go the same
way. On the old machine, run:

```bash
git fetch --tags && git checkout 2026.11.12
deploy/move.sh tools-us --upgrade
```

move.sh builds the images from the checkout, without starting anything,
so the old machine stays stopped.

`--upgrade` backs blt up on the server first. It then swaps in the new
code, keeping `config/` and the previous code in `~/blt.previous`, sends
the changed images and restarts blt and every collector there on them. The
data and settings stay as they are. It stops if the server has no blt yet.

If the server can reach GitHub, a plain checkout there and
`./deploy/up.sh` works too, as on any machine.
