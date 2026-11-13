# A remote collector on Windows

A Windows server inside a network blt cannot reach can be a remote
collector (docs/remote-collector.md) with no podman, no ssh and no
internet. All it needs is to reach blt's server on 443. It runs as a
Windows service, with its own Python, and takes the runs for the
sources that name it, as a podman collector does.

```
blt's server (BLT_UI_TLS=on)                       Windows server in the production network
  https://<server>:443                                blt-collector-uk (Windows service)
    /prefect/api  -> Prefect   <----- HTTPS ------      asks Prefect for work
    /api          -> blt's API <----- HTTPS ------      posts what it collects
    /downloads/   -> the bundle                         --> NetBackup / Commvault / Rubrik it can reach
```

## On blt's server

HTTPS for the UI, with the pass-through to Prefect and the API
(docs/production-setup.md, "HTTPS for the UI, and collectors over 443"),
in `config/blt.env`:

```
BLT_UI_TLS=on
BLT_UI_ORIGIN=https://<this server's name>
BLT_PREFECT=true
```

Then `./deploy/up.sh`. With HTTPS on it also builds the Windows bundle
(the `blt-collector-windows` image; about a minute the first time,
cached after that), and ends with:

```
Remote collectors reach blt at https://blt.example.com (/prefect/api and /api pass through)
The UI's certificate, SHA-256: 3F:A1:...:9C
Windows collector: https://blt.example.com/downloads/blt-collector-windows.zip
```

Note the fingerprint: the Windows server checks blt's certificate
against it. **Administration -> Sources** also has a "Download the
Windows collector" link, under Collectors.

`BLT_WINDOWS_COLLECTOR=off` in config/blt.env leaves the bundle out.
`BLT_SKIP_BUILD=true` serves the one already built; `deploy/move.sh`
takes it along with the other images.

To build the bundle without starting anything (only podman needed):

```bash
deploy/windows/build-bundle.sh            # -> dist/blt-collector-windows-<version>.zip
```

## On the Windows server

Windows Server 2019 or later, as an Administrator.

1. **Get the bundle.** In a browser on the server, open
   `https://<blt server>/downloads/blt-collector-windows.zip` (accept the
   certificate warning if blt's is self-signed), or copy the zip over
   RDP. Its SHA-256 is beside it, at `...zip.sha256`.

2. **Unpack it.** In PowerShell, as Administrator:

   ```powershell
   cd $HOME\Downloads
   Unblock-File .\blt-collector-windows.zip
   Expand-Archive .\blt-collector-windows.zip -DestinationPath .
   cd .\blt-collector-windows-<version>
   ```

   `Unblock-File` stops Windows treating the scripts as from the
   internet. Or run the installer with
   `powershell -ExecutionPolicy Bypass -File .\install.ps1 ...`.

3. **Install the collector**, named as its sources will name it:

   ```powershell
   .\install.ps1 -Name uk -Url https://blt.example.com -TrustCertificate
   ```

   - `-TrustCertificate` fetches blt's certificate and shows its SHA-256
     fingerprint. Compare it with the one `up.sh` printed and answer `y`.
     Or give it up front, and nothing is asked:
     `-CertificateFingerprint 3F:A1:...:9C`.
   - It asks for **Prefect's `user:password`** (`PREFECT_AUTH` in blt's
     config/blt.env) and **blt's API key** (`BLT_API_KEY`). Neither is
     shown as you type, nor taken from the command line, nor logged.
   - It checks that Prefect and blt's API answer, then starts the
     service. If they do not answer yet, it says why and installs the
     service anyway; the service keeps trying.

4. **In blt**: Administration -> Sources. `uk` appears under Collectors,
   online. Point the sources it should collect at it.

### What it installs

| Where | What |
|---|---|
| `C:\Program Files\blt-collector\<version>\` | Python 3.12 with blt and everything it needs; WinSW; a copy of install.ps1 |
| `C:\ProgramData\blt-collector\<name>\collector.json` | Its settings, Prefect's password and blt's API key among them |
| `C:\ProgramData\blt-collector\<name>\ca-bundle.pem` | The CAs it trusts: Python's usual ones and blt's certificate |
| `C:\ProgramData\blt-collector\<name>\logs\` | `blt-collector-<name>.out.log` (what it does), `.err.log`, `.wrapper.log`; 10 MB each, 8 kept |
| Service `blt-collector-<name>` | "blt collector <name>": automatic (delayed) start, restarted 10, 30 and 60 seconds after a failure |

`C:\ProgramData\blt-collector` is readable by SYSTEM and Administrators
only: the settings file holds the keys to every source in Prefect, as on
a podman collector. The service runs as LocalSystem.

### Upgrading

After blt is upgraded, download the new bundle, unpack it, and run its
installer with just the name:

```powershell
.\install.ps1 -Name uk
```

It keeps the address, the password, the key and the certificate,
installs the new version beside the old, moves the service to it and
restarts it, then removes the old version. The collector must run the
same blt as the server: a flow run of a newer blt's deployments may need
code the older collector does not have.

`-Reconfigure` asks for the password and key again. `-Url`,
`-TrustCertificate` or `-CertificateFile` replace those.

### Removing it

```powershell
& 'C:\Program Files\blt-collector\<version>\install.ps1' -Name uk -Uninstall
```

The service, its settings and logs go; and blt from Program Files when
no other collector uses it. Point its sources at another collector
first.

## Certificates

The collector always checks blt's certificate. It never turns checking
off. What it checks against:

- **blt's self-signed certificate** (`up.sh` made it): `-TrustCertificate`.
  The certificate is fetched once, shown, and trusted only once you
  confirm its fingerprint. Only that fetch accepts any certificate, so
  that it can show it to you.
- **A certificate from your own CA**: `-CertificateFile ca.pem`, your
  CA's certificate (PEM or DER). A chain in one PEM file is fine.
- **A publicly signed certificate**: neither. Python's usual CAs
  (certifi's) already cover it.

The certificate goes into `ca-bundle.pem` after certifi's CAs, and both
`SSL_CERT_FILE` (httpx, used by blt and every SDK) and
`PREFECT_API_SSL_CERT_FILE` (Prefect, its WebSockets included) point
there. Rubrik, NetBackup and Commvault are checked as before: their
certificates against certifi's CAs, or their own CA file where a source
has one.

When blt's certificate changes (a new self-signed one after
`config/tls` was cleared, or a CA's), run `install.ps1 -Name uk
-TrustCertificate` again.

## When it does not work

`C:\ProgramData\blt-collector\<name>\logs\blt-collector-<name>.out.log`
says what it is doing. While it cannot reach Prefect it says why every
five minutes:

- `ConnectError ... certificate verify failed`: the certificate it
  trusts is not blt's. Run install.ps1 with `-TrustCertificate` again.
- `ConnectError ... getaddrinfo failed` or `timed out`: the name, or
  port 443, is not reachable from this server.
- `401 Unauthorized`: Prefect's password is wrong. `-Reconfigure`.

Check again by hand, as Administrator, without touching the service:

```powershell
& 'C:\Program Files\blt-collector\<version>\python\python.exe' -m blt.flows.collector --settings C:\ProgramData\blt-collector\uk\collector.json --check
```

`Get-Service blt-collector-uk`, and Restart-Service, as for any service.

## Known limits

- Windows Server 2019 is what it is written for. CI runs it on Windows
  Server 2022 (GitHub no longer has 2019 runners), with Windows
  PowerShell 5.1 and .NET Framework 4.8 as on 2019.
- A collector and its server must run the same blt version: upgrade the
  collector after each blt upgrade.
- The bundle is built on Linux for Windows (uv, `--python-platform`),
  with uv.lock's versions; the build fails if any package has no Windows
  wheel. Python is python-build-standalone's 3.12; the service wrapper is
  WinSW 2.12 (MIT).
