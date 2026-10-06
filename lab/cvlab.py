"""The lab rig: make a CommServe produce jobs for blt to collect.

Not part of blt. It only borrows blt's Commvault login (same .env file,
same token renewal) and then drives the CommServe's REST API directly.

What it sets up is deliberately small and all on the CommServe's *own*
file-system client, so it needs no other machine: a few subclients named
blt-lab-*, each pointing at a folder that already exists on a Windows
CommServe, all under an existing plan. `noise` then runs backups against
them in ways that end differently - completed, failed, killed, suspended
then resumed - because that variety is what blt has to get right.

A second, optional stage adds podman containers on this machine as
extra clients (blt-lab-01, -02, ...), each a bare Linux box that installs
the file system agent on first start and registers itself, so jobs also
differ by client. They publish no ports: the agent tunnels out to the
CommServe.

    lab/00-preflight.sh [commcell]     look, change nothing
    lab/10-setup.sh     [commcell]     create the blt-lab-* subclients
    lab/20-noise.sh     [commcell]     run a round of jobs (--loop MIN to repeat)
    lab/30-clients.sh   [commcell]     start container clients (--count N)
    lab/90-teardown.sh  [commcell]     remove everything named blt-lab-*
"""

from __future__ import annotations

import argparse
import hashlib
import socket
import subprocess
import sys
import tarfile
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from sdk_primer import APIClient, SDKError, configure_logging

from blt.collector.settings import commcell_env_path, load_settings
from blt.collector.tokens import env_token_saver
from blt.commvault.auth import TokenSet
from blt.commvault.client import CommvaultClient

PREFIX = "blt-lab-"

# name suffix -> (folder on the CommServe, what it is for)
SUBCLIENTS: dict[str, tuple[str, str]] = {
    "etc": (r"C:\Windows\System32\drivers\etc", "a handful of tiny files: finishes in seconds"),
    "fonts": (r"C:\Windows\Fonts", "a few hundred MB: runs long enough to suspend or kill"),
    "drivers": (r"C:\Windows\System32\drivers", "mid-sized: a second ordinary job"),
    "missing": (
        r"C:\blt-lab\does-not-exist",
        "no such folder: a job that does not complete cleanly",
    ),
}
LONG_RUNNING = "fonts"

# Stage 2: container clients.
LAB_DIR = Path(__file__).resolve().parent
MEDIA_DIR = LAB_DIR / "media"
IMAGE = "blt-lab-agent"
MEDIA_PACKAGE = "LinuxFileServer64.tar"
CONTAINER_SUBCLIENT = PREFIX + "data"
CONTAINER_CONTENT = "/data"

ACTIVE = {"running", "waiting", "pending", "queued", "suspended", "suspend pending",
          "kill pending", "interrupt pending"}  # fmt: skip


class Lab:
    def __init__(self, api: APIClient, plan_name: str) -> None:
        self.api = api
        self.plan_name = plan_name
        info = self.get("/CommServ")
        self.cs_name: str = info["commcell"]["commCellName"]
        self.version: str = info.get("csVersionInfo", "?")
        clients = self.get("/Client").get("clientProperties", [])
        match = [
            c["client"]["clientEntity"]
            for c in clients
            if c["client"]["clientEntity"]["clientName"].lower() == self.cs_name.lower()
        ]
        if not match:
            raise SystemExit(f"The CommServe has no client named {self.cs_name!r} for itself.")
        self.client_id: int = match[0]["clientId"]

    # -- plumbing ---------------------------------------------------------

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.api.request(method, path, json=body)
        try:
            data = response.json()
        except ValueError:
            raise SystemExit(
                f"{method} {path} did not return JSON - is the CommServe in maintenance?"
            ) from None
        return data if isinstance(data, dict) else {"_": data}

    def get(self, path: str) -> dict[str, Any]:
        return self.call("GET", path)

    @staticmethod
    def error_in(data: dict[str, Any]) -> str | None:
        """Commvault reports most failures as a 200 with an error inside.
        The places it puts one vary by endpoint; this checks the usual ones."""
        candidates: list[dict[str, Any]] = [data]
        for key in ("response", "errorList", "errList"):
            value = data.get(key)
            if isinstance(value, list):
                candidates.extend(v for v in value if isinstance(v, dict))
            elif isinstance(value, dict):
                candidates.append(value)
        for item in candidates:
            code = item.get("errorCode", 0)
            if code not in (0, "0", None):
                text = (
                    item.get("errorString") or item.get("errorMessage") or item.get("errLogMessage")
                )
                return f"error {code}: {text or item}"
        return None

    # -- lookups ----------------------------------------------------------

    def lab_clients(self) -> dict[str, int]:
        """Container clients registered on the CommServe: name -> client id."""
        found = {}
        for item in self.get("/Client").get("clientProperties", []):
            entity = item["client"]["clientEntity"]
            if entity["clientName"].startswith(PREFIX):
                found[entity["clientName"]] = entity["clientId"]
        return found

    def lab_subclients(self) -> dict[str, dict[str, Any]]:
        """Existing blt-lab-* subclients, by label. On the CommServe's own
        client the label is the subclient name (blt-lab-etc, ...); on a
        container client it is "<client>/data"."""
        found = {}
        for client_name, client_id in {self.cs_name: self.client_id, **self.lab_clients()}.items():
            data = self.get(f"/Subclient?clientId={client_id}")
            for item in data.get("subClientProperties", []):
                entity = item.get("subClientEntity", {})
                name = entity.get("subclientName", "")
                if entity.get("appName") != "File System" or not name.startswith(PREFIX):
                    continue
                label = name if client_id == self.client_id else f"{client_name}/data"
                found[label] = entity
        return found

    def plans(self) -> dict[str, int]:
        data = self.get("/V4/Plan/Summary")
        return {p["plan"]["name"]: p["plan"]["id"] for p in data.get("plans", [])}

    def job(self, job_id: int) -> dict[str, Any] | None:
        jobs = self.get(f"/Job/{job_id}").get("jobs") or []
        return jobs[0].get("jobSummary") if jobs else None

    def active_jobs(self) -> list[dict[str, Any]]:
        body = {
            "scope": 1,
            "category": 1,
            "pagingConfig": {"sortField": "jobId", "sortDirection": 1, "offset": 0, "limit": 200},
            "jobFilter": {"completedJobLookupTime": 0, "showAgedJobs": False},
        }
        return [j["jobSummary"] for j in self.call("POST", "/Jobs", body).get("jobs") or []]

    # -- commands ---------------------------------------------------------

    def preflight(self) -> bool:
        ok = True
        logger.info(
            "CommServe {} version {} (client id {})", self.cs_name, self.version, self.client_id
        )

        agents = self.get(f"/Agent?clientId={self.client_id}").get("agentProperties", [])
        has_fs = any(
            a["idaEntity"].get("appName") == "File System"
            and not a.get("AgentProperties", {}).get("isMarkedDeleted")
            for a in agents
        )
        logger.log("INFO" if has_fs else "ERROR", "File System agent on the CommServe: {}", has_fs)
        ok &= has_fs

        plans = self.plans()
        has_plan = self.plan_name in plans
        logger.log(
            "INFO" if has_plan else "ERROR",
            "Plan {!r}: {} (plans here: {})",
            self.plan_name,
            "found" if has_plan else "NOT FOUND",
            ", ".join(plans) or "none",
        )
        ok &= has_plan

        storage = self.get("/V4/Storage/Disk").get("diskStorage", [])
        online = [s["name"] for s in storage if s.get("status") == "Online"]
        logger.log(
            "INFO" if online else "ERROR", "Online disk storage: {}", ", ".join(online) or "none"
        )
        ok &= bool(online)

        existing = self.lab_subclients()
        logger.info("Lab subclients present: {}", ", ".join(existing) or "none")
        active = self.active_jobs()
        logger.info("Jobs active right now: {}", len(active))
        logger.log("INFO" if ok else "ERROR", "Preflight {}", "passed" if ok else "FAILED")
        return ok

    def create_subclient(
        self, client_name: str, name: str, path: str, purpose: str, plan_id: int
    ) -> bool:
        body = {
            "subClientProperties": {
                "contentOperationType": 2,
                "subClientEntity": {
                    "clientName": client_name,
                    "appName": "File System",
                    "instanceName": "DefaultInstanceName",
                    "backupsetName": "defaultBackupSet",
                    "subclientName": name,
                },
                "content": [{"path": path}],
                "commonProperties": {"enableBackup": True, "description": f"blt lab: {purpose}"},
                "planEntity": {"planName": self.plan_name, "planId": plan_id},
            }
        }
        error = self.error_in(self.call("POST", "/Subclient", body))
        if error:
            logger.error("Creating {} on {} failed - {}", name, client_name, error)
            return False
        logger.info("Created {} on {} -> {}", name, client_name, path)
        return True

    def setup(self) -> None:
        plans = self.plans()
        if self.plan_name not in plans:
            raise SystemExit(f"Plan {self.plan_name!r} not found; have: {', '.join(plans)}")
        existing = self.lab_subclients()
        for suffix, (path, purpose) in SUBCLIENTS.items():
            name = PREFIX + suffix
            if name in existing:
                logger.info("{} already exists (id {})", name, existing[name]["subclientId"])
                continue
            self.create_subclient(self.cs_name, name, path, purpose, plans[self.plan_name])

        self.read_back()

    def read_back(self) -> None:
        """Report every lab subclient as the CommServe now has it, rather
        than trust the replies to the calls that created them."""
        for name, entity in self.lab_subclients().items():
            props = self.get(f"/Subclient/{entity['subclientId']}")["subClientProperties"][0]
            plan = props.get("planEntity", {}).get("planName")
            content = [c.get("path") for c in props.get("content", [])]
            logger.log(
                "INFO" if plan else "WARNING",
                "{} (id {}): plan={} content={}",
                name,
                entity["subclientId"],
                plan or "NONE - backups will not have anywhere to go",
                content,
            )

    def start_backup(self, name: str, subclient_id: int, level: str) -> int | None:
        try:
            data = self.call("POST", f"/Subclient/{subclient_id}/action/backup?backupLevel={level}")
        except SDKError as exc:
            # Commvault answers 409 when the subclient already has a
            # backup queued or running - e.g. two noise rounds overlapping.
            # One subclient not starting is no reason to abandon the round.
            reason = "already has a backup in progress" if "409" in str(exc) else str(exc)
            logger.warning("{}: {} backup not started - {}", name, level, reason)
            return None
        job_ids = data.get("jobIds") or []
        if not job_ids:
            logger.error(
                "{}: {} backup did not start - {}", name, level, self.error_in(data) or data
            )
            return None
        logger.info("{}: {} backup started as job {}", name, level, job_ids[0])
        return int(job_ids[0])

    def job_action(self, job_id: int, action: str) -> None:
        data = self.call("POST", f"/Job/{job_id}/action/{action}")
        error = self.error_in(data)
        logger.log(
            "WARNING" if error else "INFO", "job {}: {} {}", job_id, action, error or "requested"
        )

    def wait_for_status(self, job_id: int, wanted: set[str], timeout: float) -> str:
        """Poll until the job's status is one of `wanted` (lower-case),
        it finishes, or the timeout passes. Returns the last status seen."""
        deadline = time.monotonic() + timeout
        status = "unknown"
        while time.monotonic() < deadline:
            summary = self.job(job_id)
            status = (summary or {}).get("status", "gone")
            if status.lower() in wanted or status.lower() not in ACTIVE:
                return status
            time.sleep(3)
        return status

    def noise_round(self, level: str, drama: bool) -> list[int]:
        subclients = self.lab_subclients()
        if not subclients:
            raise SystemExit("No blt-lab-* subclients - run lab/10-setup.sh first.")
        touch_container_data()
        started: list[int] = []

        long_name = PREFIX + LONG_RUNNING
        for name, entity in subclients.items():
            if drama and name == long_name:
                continue  # handled below
            job_id = self.start_backup(name, entity["subclientId"], level)
            if job_id:
                started.append(job_id)

        if drama and long_name in subclients:
            subclient_id = subclients[long_name]["subclientId"]
            # A full, so there is enough work for the job to still be
            # running when it is interfered with.
            job_id = self.start_backup(long_name, subclient_id, "Full")
            if job_id:
                started.append(job_id)
                status = self.wait_for_status(job_id, {"running"}, timeout=120)
                if status.lower() == "running":
                    self.job_action(job_id, "pause")
                    logger.info(
                        "job {}: now {}", job_id, self.wait_for_status(job_id, {"suspended"}, 60)
                    )
                    time.sleep(20)  # long enough for a blt run to catch it suspended
                    self.job_action(job_id, "resume")
                    self.wait_for_status(job_id, {"running"}, 60)
                    self.job_action(job_id, "kill")
                else:
                    logger.warning("job {} was {} before it could be suspended", job_id, status)
        return started

    def report(self, job_ids: list[int], timeout: float) -> None:
        deadline = time.monotonic() + timeout
        pending = set(job_ids)
        last: dict[int, str] = {}
        while pending and time.monotonic() < deadline:
            for job_id in sorted(pending):
                summary = self.job(job_id) or {}
                last[job_id] = summary.get("status", "gone")
                if last[job_id].lower() not in ACTIVE:
                    pending.discard(job_id)
            if pending:
                time.sleep(5)
        for job_id in job_ids:
            logger.info("job {}: {}", job_id, last.get(job_id, "unknown"))
        if pending:
            logger.warning("Still active after {:.0f}s: {}", timeout, sorted(pending))

    # -- stage 2: container clients ---------------------------------------

    def authcode(self) -> str:
        """A CommCell install authcode: what lets an installer register a
        client without a user name and password. Asking for one turns the
        feature on for the CommCell and returns a fresh code."""
        data = self.call("POST", "/Organization/0/Authtoken")
        code = data.get("organizationProperties", {}).get("authCode")
        if not code:
            raise SystemExit(f"Could not get an install authcode - {self.error_in(data) or data}")
        return str(code)

    def ensure_media(self) -> None:
        """The Commvault Unix install media, unpacked at lab/media/Unix.
        Downloaded from the public URL the CommServe itself advertises
        for the Linux file server package, and checked against the
        CommServe's checksum before it is unpacked."""
        if (MEDIA_DIR / "Unix" / "silent_install").is_file():
            return
        packages = self.get("/V4/commcell/available-packages").get("pkgList", [])
        package = next((p for p in packages if p.get("fileName") == MEDIA_PACKAGE), None)
        if package is None:
            raise SystemExit(
                f"The CommServe does not list {MEDIA_PACKAGE} as a downloadable package."
            )
        MEDIA_DIR.mkdir(exist_ok=True)
        tarball = MEDIA_DIR / MEDIA_PACKAGE
        if not tarball.is_file():
            logger.info("Downloading {} ({}) ...", MEDIA_PACKAGE, package.get("fileSize"))
            with httpx.stream(
                "GET", package["downloadURL"], timeout=600, follow_redirects=True
            ) as r:
                r.raise_for_status()
                with open(tarball, "wb") as handle:
                    for chunk in r.iter_bytes(1 << 20):
                        handle.write(chunk)
        digest = hashlib.sha256()
        with open(tarball, "rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
        if digest.hexdigest() != package.get("checksum"):
            raise SystemExit(
                f"{tarball} does not match the CommServe's checksum - delete it and retry."
            )
        logger.info("Unpacking {} ...", MEDIA_PACKAGE)
        with tarfile.open(tarball) as archive:
            archive.extractall(MEDIA_DIR, filter="data")

    def clients_up(self, count: int, cs_address: str) -> None:
        plans = self.plans()
        if self.plan_name not in plans:
            raise SystemExit(f"Plan {self.plan_name!r} not found; have: {', '.join(plans)}")
        self.ensure_media()
        if podman("image", "exists", IMAGE).returncode != 0:
            logger.info("Building image {} ...", IMAGE)
            podman("build", "-q", "-t", IMAGE, str(LAB_DIR / "client-image"), check=True)

        wanted = [f"{PREFIX}{n:02d}" for n in range(1, count + 1)]
        code: str | None = None
        # One at a time, each registered before the next starts: three
        # installs registering at once made one of them fail with
        # "Failed in initializing thread pool" on the first real run.
        for name in wanted:
            registered = name in self.lab_clients()
            exists = podman("container", "exists", name).returncode == 0
            if exists and registered:
                podman("start", name, check=True)
                logger.info("{}: already a client, container started", name)
                continue
            if exists:
                # Left over from an install that never registered.
                podman("rm", "-f", "-t", "0", name, check=True)
                logger.info("{}: removed a container that had not registered", name)
            if registered:
                logger.warning(
                    "{} is registered on the CommServe but has no container - run "
                    "lab/90-teardown.sh first, or it will fail to register again.",
                    name,
                )
            code = code or self.authcode()
            for attempt in (1, 2):
                podman(
                    "run", "-d", "--name", name, "--hostname", name,
                    "--add-host", f"{self.cs_name}:{cs_address}",
                    "-v", f"{MEDIA_DIR}:/media:ro,z",
                    "-e", f"LAB_CLIENT_NAME={name}",
                    "-e", f"LAB_CS_NAME={self.cs_name}",
                    "-e", f"LAB_CS_HOST={self.cs_name}",
                    "-e", f"LAB_AUTHCODE={code}",
                    IMAGE,
                    check=True,
                )  # fmt: skip
                logger.info("{}: container started, installing the agent", name)
                if self.wait_for_client(name):
                    logger.info("{}: registered", name)
                    break
                logger.error(
                    "{}: did not register -\n{}", name, podman("logs", name).stdout.strip()
                )
                if attempt == 1:
                    logger.info("{}: trying once more", name)
                    podman("rm", "-f", "-t", "0", name, check=True)

        existing = self.lab_subclients()
        for name in wanted:
            if name in self.lab_clients() and f"{name}/data" not in existing:
                self.create_subclient(
                    name, CONTAINER_SUBCLIENT, CONTAINER_CONTENT, "container client data",
                    plans[self.plan_name],
                )  # fmt: skip
        self.read_back()

    def wait_for_client(self, name: str, timeout: float = 600) -> bool:
        """True once `name` is a client on the CommServe; False as soon as
        its container reports the install did not end cleanly, or on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(10)
            if name in self.lab_clients():
                return True
            if "INSTALL FAILED" in podman("logs", name).stdout:
                return False
        return False

    def teardown(self) -> None:
        subclients = self.lab_subclients()
        mine = {s["subclientId"] for s in subclients.values()}
        for summary in self.active_jobs():
            if summary.get("subclient", {}).get("subclientId") in mine:
                self.job_action(summary["jobId"], "kill")
        for name, entity in subclients.items():
            if entity["clientId"] != self.client_id:
                continue  # goes with its client, below
            data = self.call("DELETE", f"/Subclient/{entity['subclientId']}")
            error = self.error_in(data)
            logger.log("ERROR" if error else "INFO", "Deleted subclient {} {}", name, error or "")
        for name, client_id in self.lab_clients().items():
            data = self.call("DELETE", f"/Client/{client_id}?forceDelete=1")
            error = self.error_in(data)
            logger.log("ERROR" if error else "INFO", "Deleted client {} {}", name, error or "")
        for name in lab_containers(all_states=True):
            podman("rm", "-f", "-t", "0", name)
            logger.info("Removed container {}", name)

        left = [*self.lab_subclients(), *self.lab_clients()]
        if left:
            logger.error("Still present on the CommServe: {}", ", ".join(left))
        else:
            logger.info("Nothing named {}* is left on the CommServe.", PREFIX)


def podman(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["podman", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"podman {' '.join(args[:3])} ... failed: {result.stderr.strip()}")
    return result


def lab_containers(all_states: bool = False) -> list[str]:
    args = ["ps", "--format", "{{.Names}}", "--filter", f"name=^{PREFIX}"]
    if all_states:
        args.insert(1, "-a")
    return sorted(podman(*args).stdout.split())


def touch_container_data() -> None:
    """Change a file in every running container client, so the next
    incremental has something to back up."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    for name in lab_containers():
        podman("exec", name, "sh", "-c", f"echo 'noise round at {stamp}' >> /data/notes.txt")


def main() -> None:
    parser = argparse.ArgumentParser(prog="cvlab", description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["preflight", "setup", "noise", "clients", "teardown"])
    parser.add_argument("--commcell", default="cv-toaster")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--plan", default="Standard Plan")
    parser.add_argument("--count", type=int, default=3, help="clients: how many containers")
    parser.add_argument("--level", default="auto", choices=["auto", "Full", "Incremental"])
    parser.add_argument(
        "--no-drama", action="store_true", help="plain backups only: no suspend/kill"
    )
    parser.add_argument("--loop", type=float, metavar="MINUTES", help="repeat a round this often")
    parser.add_argument("--rounds", type=int, default=0, help="with --loop: stop after N rounds")
    parser.add_argument(
        "--wait", type=float, default=600, help="seconds to wait for a round's jobs"
    )
    args = parser.parse_args()

    configure_logging(level="INFO")
    settings = load_settings(args.commcell, args.config_dir)
    if not settings.cv_access_token:
        raise SystemExit("The lab scripts need CV_ACCESS_TOKEN in the CommCell's .env file.")
    tokens = TokenSet(
        settings.cv_access_token.get_secret_value(),
        settings.cv_refresh_token.get_secret_value() if settings.cv_refresh_token else None,
        settings.cv_token_expires_at,
        settings.cv_token_renewable_until,
    )
    try:
        with CommvaultClient(
            settings.cv_base_url,
            access_token=tokens,
            on_token_renew=env_token_saver(commcell_env_path(args.commcell, args.config_dir)),
            verify_tls=settings.cv_verify_tls,
            ca_bundle=settings.cv_ca_bundle,
        ) as commvault:
            lab = Lab(commvault.api, args.plan)
            if args.command == "preflight":
                sys.exit(0 if lab.preflight() else 1)
            elif args.command == "setup":
                lab.setup()
            elif args.command == "clients":
                host = urllib.parse.urlsplit(settings.cv_base_url).hostname or ""
                lab.clients_up(args.count, socket.gethostbyname(host))
            elif args.command == "teardown":
                lab.teardown()
            else:
                round_number = 0
                while True:
                    round_number += 1
                    # First round takes fulls (nothing to be incremental
                    # against yet); later ones are incrementals.
                    level = args.level
                    if level == "auto":
                        level = "Full" if round_number == 1 else "Incremental"
                    logger.info("--- round {} ({}) ---", round_number, level)
                    lab.report(lab.noise_round(level, drama=not args.no_drama), args.wait)
                    if not args.loop or (args.rounds and round_number >= args.rounds):
                        break
                    time.sleep(args.loop * 60)
    except SDKError as exc:
        logger.error("{}", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
