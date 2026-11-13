blt remote collector for Windows
================================

A blt remote collector as a Windows service: it takes blt's runs for the
sources only this server can reach, and sends what it collects to blt
over HTTPS (port 443). It brings its own Python; nothing else is needed.

As Administrator, in PowerShell, in this folder:

    .\install.ps1 -Name <name> -Url https://<blt server> -TrustCertificate

-TrustCertificate shows blt's certificate fingerprint: check it is the one
blt's ./deploy/up.sh printed. Leave it out if blt's certificate is from a
public CA; use -CertificateFile <file> for one from your own CA.

You are asked for Prefect's user:password and blt's API key. They are
kept in C:\ProgramData\blt-collector\<name>\collector.json, readable only
by SYSTEM and Administrators.

Upgrade: unpack the new bundle and run its install.ps1 -Name <name>.
Remove:  .\install.ps1 -Name <name> -Uninstall

Logs: C:\ProgramData\blt-collector\<name>\logs

blt's docs/windows-collector.md has the rest.
