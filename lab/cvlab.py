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
    lab/40-sql.sh       [commcell]     start a SQL Server client (--databases N)
    lab/45-sql-backup.sh [commcell]    back up its databases (--levels Full,...)
    lab/50-sql-validate.sh [commcell]  compare SQL Server's databases with what
                                       Commvault has backed up (read-only)
    lab/90-teardown.sh  [commcell]     remove everything named blt-lab-*
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
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
from sdk_primer import SDKError, configure_logging

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

# Stage 3: a SQL Server client.
# Which SQL Server container the sql-* commands act on. A default; main()
# replaces it from --sql-client, so a second one (blt-lab-sql-02, ...)
# can be stood up and driven with the same commands.
SQL_CLIENT = PREFIX + "sql-01"
SQL_IMAGE = "blt-lab-sql"
SQL_MEDIA_PACKAGE = "LinuxMSSQLServer64.tar"
SQL_MEDIA_DIR = MEDIA_DIR / "sql"
SQL_CREDENTIAL = PREFIX + "sql-sa"
SQL_SYSTEM_DATABASES = {"master", "model", "msdb"}
STATE_DIR = LAB_DIR / ".state"

# Commands that never change the CommServe (the sql-add-db / -offline /
# -online ones change only the lab's own SQL Server container).
READ_ONLY_COMMANDS = {
    "preflight", "sql-report", "sql-validate", "sql-add-db", "sql-offline", "sql-online",
}  # fmt: skip

ACTIVE = {"running", "waiting", "pending", "queued", "suspended", "suspend pending",
          "kill pending", "interrupt pending"}  # fmt: skip


class Lab:
    """Everything the lab does to the CommServe goes through blt's
    Commvault SDK (`self.cv`, a CommvaultClient built with
    allow_changes=True - the lab is the one place that is meant to change
    things). Nothing here builds a URL."""

    def __init__(self, cv: CommvaultClient, plan_name: str) -> None:
        self.cv = cv
        self.plan_name = plan_name
        info = cv.commcell.info()
        self.cs_name: str = info["commcell"]["commCellName"]
        self.version: str = info.get("csVersionInfo", "?")
        own = cv.clients.find(self.cs_name)
        if own is None:
            raise SystemExit(f"The CommServe has no client named {self.cs_name!r} for itself.")
        self.client_id: int = own["clientId"]

    # -- lookups ----------------------------------------------------------

    def lab_clients(self) -> dict[str, int]:
        """Container clients registered on the CommServe: name -> client id."""
        return {
            entity["clientName"]: entity["clientId"]
            for entity in self.cv.clients.list()
            if entity["clientName"].startswith(PREFIX)
        }

    def lab_subclients(self) -> dict[str, dict[str, Any]]:
        """Existing blt-lab-* subclients, by label. On the CommServe's own
        client the label is the subclient name (blt-lab-etc, ...); on a
        container client it is "<client>/data"."""
        found = {}
        for client_name, client_id in {self.cs_name: self.client_id, **self.lab_clients()}.items():
            for entity in self.cv.subclients.list(client_id):
                name = entity.get("subclientName", "")
                if entity.get("appName") != "File System" or not name.startswith(PREFIX):
                    continue
                label = name if client_id == self.client_id else f"{client_name}/data"
                found[label] = entity
        return found

    def plans(self) -> dict[str, int]:
        return self.cv.plans.ids()

    def job_status(self, job_id: int) -> str:
        job = self.cv.jobs.get(job_id)
        return job.status if job else "gone"

    # -- commands ---------------------------------------------------------

    def preflight(self) -> bool:
        ok = True
        logger.info(
            "CommServe {} version {} (client id {})", self.cs_name, self.version, self.client_id
        )

        has_fs = "File System" in self.cv.clients.agents(self.client_id)
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

        online = [s["name"] for s in self.cv.storage.disk() if s.get("status") == "Online"]
        logger.log(
            "INFO" if online else "ERROR", "Online disk storage: {}", ", ".join(online) or "none"
        )
        ok &= bool(online)

        existing = self.lab_subclients()
        logger.info("Lab subclients present: {}", ", ".join(existing) or "none")
        logger.info("Jobs active right now: {}", len(self.cv.jobs.active()))
        logger.log("INFO" if ok else "ERROR", "Preflight {}", "passed" if ok else "FAILED")
        return ok

    def create_subclient(
        self, client_name: str, name: str, path: str, purpose: str, plan_id: int
    ) -> bool:
        try:
            self.cv.subclients.create(
                client_name,
                name,
                content=[{"path": path}],
                plan={"planName": self.plan_name, "planId": plan_id},
                description=f"blt lab: {purpose}",
            )
        except SDKError as exc:
            logger.error("Creating {} on {} failed - {}", name, client_name, exc)
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
            props = self.cv.subclients.get(entity["subclientId"])
            plan = (props.get("planEntity") or {}).get("planName")
            content = [c.get("path") for c in props.get("content") or []]
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
            job_id = self.cv.subclients.backup(subclient_id, level)
        except SDKError as exc:
            # Commvault answers 409 when the subclient already has a
            # backup queued or running - e.g. two noise rounds overlapping.
            # One subclient not starting is no reason to abandon the round.
            reason = "already has a backup in progress" if "409" in str(exc) else str(exc)
            logger.warning("{}: {} backup not started - {}", name, level, reason)
            return None
        logger.info("{}: {} backup started as job {}", name, level, job_id)
        return job_id

    def job_action(self, job_id: int, action: str) -> None:
        """suspend, resume or kill a job; a refusal is logged, not fatal
        (the job may simply have finished first)."""
        try:
            getattr(self.cv.jobs, action)(job_id)
        except SDKError as exc:
            logger.warning("job {}: {} refused - {}", job_id, action, exc)
        else:
            logger.info("job {}: {} requested", job_id, action)

    def wait_for_status(self, job_id: int, wanted: set[str], timeout: float) -> str:
        """Poll until the job's status is one of `wanted` (lower-case),
        it finishes, or the timeout passes. Returns the last status seen."""
        deadline = time.monotonic() + timeout
        status = "unknown"
        while time.monotonic() < deadline:
            status = self.job_status(job_id)
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
                    self.job_action(job_id, "suspend")
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
                last[job_id] = self.job_status(job_id)
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
        try:
            return self.cv.commcell.install_authcode()
        except SDKError as exc:
            raise SystemExit(f"Could not get an install authcode - {exc}") from None

    def ensure_media(self, package_name: str = MEDIA_PACKAGE, dest: Path = MEDIA_DIR) -> None:
        """A Commvault Unix install package, unpacked at `dest`/Unix.
        Downloaded from the public URL the CommServe itself advertises
        for it, and checked against the CommServe's checksum before it
        is unpacked."""
        if (dest / "Unix" / "silent_install").is_file():
            return
        packages = self.cv.commcell.packages()
        package = next((p for p in packages if p.get("fileName") == package_name), None)
        if package is None:
            raise SystemExit(
                f"The CommServe does not list {package_name} as a downloadable package."
            )
        dest.mkdir(parents=True, exist_ok=True)
        tarball = dest / package_name
        if not tarball.is_file():
            logger.info("Downloading {} ({}) ...", package_name, package.get("fileSize"))
            with httpx.stream(
                "GET", package["downloadURL"], timeout=900, follow_redirects=True
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
        logger.info("Unpacking {} ...", package_name)
        with tarfile.open(tarball) as archive:
            archive.extractall(dest, filter="data")

    def ensure_image(self, image: str, context: Path) -> None:
        if podman("image", "exists", image).returncode != 0:
            logger.info("Building image {} ...", image)
            podman("build", "-q", "-t", image, str(context), check=True)

    def register_container(
        self,
        name: str,
        image: str,
        media: Path,
        cs_address: str,
        code: str,
        extra_env: dict[str, str] | None = None,
        timeout: float = 600,
        run_args: tuple[str, ...] = (),
    ) -> bool:
        """Start a container that installs its agent and registers itself
        as client `name`; True once the CommServe lists it. One retry."""
        env = {
            "LAB_CLIENT_NAME": name,
            "LAB_CS_NAME": self.cs_name,
            "LAB_CS_HOST": self.cs_name,
            "LAB_AUTHCODE": code,
            **(extra_env or {}),
        }
        env_args = [arg for key, value in env.items() for arg in ("-e", f"{key}={value}")]
        for attempt in (1, 2):
            podman(
                "run", "-d", "--name", name, "--hostname", name,
                "--add-host", f"{self.cs_name}:{cs_address}",
                "-v", f"{media}:/media:ro,z",
                *run_args,
                *env_args,
                image,
                check=True,
            )  # fmt: skip
            logger.info("{}: container started, installing the agent", name)
            if self.wait_for_client(name, timeout):
                logger.info("{}: registered", name)
                return True
            logger.error("{}: did not register -\n{}", name, podman("logs", name).stdout.strip())
            if attempt == 1:
                logger.info("{}: trying once more", name)
                podman("rm", "-f", "-t", "0", name, check=True)
        return False

    def clients_up(self, count: int, cs_address: str) -> None:
        plans = self.plans()
        if self.plan_name not in plans:
            raise SystemExit(f"Plan {self.plan_name!r} not found; have: {', '.join(plans)}")
        self.ensure_media()
        self.ensure_image(IMAGE, LAB_DIR / "client-image")

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
            self.register_container(name, IMAGE, MEDIA_DIR, cs_address, code)

        existing = self.lab_subclients()
        for name in wanted:
            if name in self.lab_clients() and f"{name}/data" not in existing:
                self.create_subclient(
                    name, CONTAINER_SUBCLIENT, CONTAINER_CONTENT, "container client data",
                    plans[self.plan_name],
                )  # fmt: skip
        self.read_back()

    # -- stage 3: a SQL Server client -------------------------------------

    def sql_up(self, cs_address: str, db_count: int) -> None:
        """One container running SQL Server with `db_count` user
        databases and the Commvault SQL Server agent, registered as
        blt-lab-sql-01. Then reports what the CommServe makes of it."""
        name = SQL_CLIENT
        registered = name in self.lab_clients()
        exists = podman("container", "exists", name).returncode == 0
        if exists and registered:
            podman("start", name, check=True)
            logger.info("{}: already a client, container started", name)
        else:
            if exists:
                podman("rm", "-f", "-t", "0", name, check=True)
                logger.info("{}: removed a container that had not registered", name)
            self.ensure_media(SQL_MEDIA_PACKAGE, SQL_MEDIA_DIR)
            self.ensure_image(SQL_IMAGE, LAB_DIR / "sql-image")
            ok = self.register_container(
                name,
                SQL_IMAGE,
                SQL_MEDIA_DIR,
                cs_address,
                self.authcode(),
                {"LAB_SA_PASSWORD": sql_sa_password(), "LAB_DB_COUNT": str(db_count)},
                timeout=900,
                # SQL Server hands backup data to the agent through shared
                # memory (VDI): 2 streams x 20 buffers x 2 MB. A container's
                # default 64 MB /dev/shm is too small, and the backup dies
                # with "OpenDevice Failed [0x80770004]" / OS error 995.
                run_args=("--shm-size=2g",),
            )
            if not ok:
                return
        self.sql_configure()
        self.sql_report()

    def sql_known_databases(self, instance_id: int) -> list[dict[str, Any]]:
        return self.cv.sql.databases(instance_id)

    def sql_validate(self, max_age_hours: float) -> bool:
        """The backup validation itself: every database SQL Server has,
        against what Commvault says it has protected. True if nothing is
        unprotected or stale.

        The "what exists" side comes from SQL Server (sys.databases), not
        from Commvault - a database Commvault has never seen is exactly
        the case a check built on Commvault's own list would miss.
        """
        client_id = self.lab_clients().get(SQL_CLIENT)
        instance = self.sql_instance(client_id) if client_id else None
        if instance is None:
            raise SystemExit(f"No SQL Server instance on {SQL_CLIENT} - run lab/40-sql.sh first.")
        known = {
            d["dbName"]: int(d.get("bkpTime") or 0)
            for d in self.sql_known_databases(instance["instanceId"])
        }
        actual = sql_databases(SQL_CLIENT)
        now = time.time()
        rows: list[tuple[str, str, str]] = []
        problems = 0
        for name in actual:
            last = known.get(name, 0)
            if name == "tempdb":
                verdict, detail = "n/a", "rebuilt at every start; never backed up"
            elif name in SQL_SYSTEM_DATABASES and name not in known:
                # Commvault backs these up (see the job) but this
                # endpoint lists user databases only, so it cannot be
                # used to prove it either way.
                verdict, detail = "unverified", "system database: not listed by /sql/databases"
            elif not last:
                verdict, detail = "UNPROTECTED", "Commvault has no backup of it"
                problems += 1
            elif now - last > max_age_hours * 3600:
                verdict = "STALE"
                detail = f"last backup {(now - last) / 3600:.1f} h ago (limit {max_age_hours:g} h)"
                problems += 1
            else:
                verdict = "ok"
                detail = "last backup " + time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(last))
            rows.append((name, verdict, detail))
        for name in sorted(set(known) - set(actual)):
            rows.append((name, "gone", "Commvault has backups; SQL Server no longer has it"))

        width = max(len(r[0]) for r in rows)
        for name, verdict, detail in rows:
            logger.log(
                "ERROR" if verdict in ("UNPROTECTED", "STALE") else "INFO",
                "{:<{w}}  {:<12} {}",
                name,
                verdict,
                detail,
                w=width,
            )
        user_dbs = [n for n in actual if n not in SQL_SYSTEM_DATABASES and n != "tempdb"]
        protected = [r for r in rows if r[1] == "ok" and r[0] in user_dbs]
        logger.log(
            "ERROR" if problems else "INFO",
            "{}: {} of {} user databases protected within {:g} h - {}",
            SQL_CLIENT,
            len(protected),
            len(user_dbs),
            max_age_hours,
            f"{problems} PROBLEM(S)" if problems else "validation passed",
        )
        return problems == 0

    def sql_backup(self, levels: list[str], wait: float) -> None:
        """Back up the SQL client's default subclient once per level, in
        order, waiting for each to finish (a differential or log backup
        needs the full before it to have completed)."""
        client_id = self.lab_clients().get(SQL_CLIENT)
        instance = self.sql_instance(client_id) if client_id else None
        if instance is None:
            raise SystemExit(f"No SQL Server instance on {SQL_CLIENT} - run lab/40-sql.sh first.")
        assert client_id is not None
        target = next(
            (
                entity
                for entity in self.cv.subclients.list(client_id)
                if entity.get("appName") == "SQL Server"
            ),
            None,
        )
        if target is None:
            raise SystemExit(f"{SQL_CLIENT} has no SQL Server subclient.")
        # Give each database a change, so differentials and log backups
        # have something in them.
        for name in sql_databases(SQL_CLIENT):
            if name.startswith("lab_db_"):
                sql_exec(SQL_CLIENT, name, "insert notes (line) values ('before a lab backup')")
        for level in levels:
            job_id = self.start_backup(
                f"{SQL_CLIENT}/{target['subclientName']}", target["subclientId"], level
            )
            if job_id is None:
                continue
            self.report([job_id], wait)
        self.sql_report()

    def sql_instance(self, client_id: int, wait: float = 0) -> dict[str, Any] | None:
        """The SQL Server instance Commvault has discovered on a client.
        Discovery runs a minute or two after the agent's services start."""
        deadline = time.monotonic() + wait
        while True:
            for instance in self.cv.instances.list(client_id):
                if instance.get("appName") == "SQL Server":
                    return instance
            if time.monotonic() >= deadline:
                return None
            time.sleep(15)

    def sql_configure(self) -> None:
        """Give the discovered instance a login it can use, and put its
        default subclient on the plan.

        On Linux the agent cannot connect as "the local system account":
        Commvault requires a SQL-authenticated sysadmin login set on the
        instance ("impersonate user"). Until that is in place the
        instance exists but is "not validated" and lists no databases.
        """
        client_id = self.lab_clients().get(SQL_CLIENT)
        if client_id is None:
            logger.error("{} is not a client on the CommServe", SQL_CLIENT)
            return
        instance = self.sql_instance(client_id, wait=300)
        if instance is None:
            logger.error(
                "No SQL Server instance discovered on {} after 5 minutes - see "
                "/var/log/commvault/Log_Files/cvd.log in the container.",
                SQL_CLIENT,
            )
            return
        instance_id = instance["instanceId"]
        mssql = self.cv.instances.get(instance_id).get("mssqlInstance", {})
        if mssql.get("MSSQLCredentialinfo", {}).get("credentialName"):
            logger.info("Instance {} already has a credential", instance["instanceName"])
        else:
            try:
                if SQL_CREDENTIAL not in self.cv.credentials.names():
                    self.cv.credentials.create(
                        SQL_CREDENTIAL,
                        "sa",
                        sql_sa_password(),
                        description="blt lab: sa on the lab SQL Server container",
                    )
                    logger.info("Credential {}: created", SQL_CREDENTIAL)
                self.cv.sql.set_instance_credential(instance, SQL_CREDENTIAL)
            except SDKError as exc:
                logger.error(
                    "Giving instance {} a login failed - {}", instance["instanceName"], exc
                )
                return
            logger.info(
                "Instance {} login set to credential {}", instance["instanceName"], SQL_CREDENTIAL
            )
            # The agent only re-checks an instance once a day by itself.
            # Restarting its services (inside our own container) makes it
            # discover and validate again now, with the login in place.
            podman("exec", SQL_CLIENT, "commvault", "restart")
            logger.info("Restarted the agent in {} so it validates the instance now", SQL_CLIENT)
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline and not self.sql_known_databases(instance_id):
                time.sleep(15)

        plans = self.plans()
        for entity in self.cv.subclients.list(client_id):
            if entity.get("appName") != "SQL Server":
                continue
            current = self.cv.subclients.get(entity["subclientId"]).get("planEntity") or {}
            if current.get("planName"):
                continue
            try:
                self.cv.subclients.set_plan(
                    entity["subclientId"],
                    {"planName": self.plan_name, "planId": plans[self.plan_name]},
                )
            except SDKError as exc:
                logger.error("Subclient {} plan not set - {}", entity.get("subclientName"), exc)
            else:
                logger.info("Subclient {} plan -> {}", entity.get("subclientName"), self.plan_name)

    def sql_report(self) -> None:
        """What SQL Server says exists, next to what Commvault knows."""
        name = SQL_CLIENT
        actual = sql_databases(name)
        logger.info("{}: SQL Server has {} databases: {}", name, len(actual), ", ".join(actual))
        client_id = self.lab_clients().get(name)
        if client_id is None:
            logger.error("{} is not a client on the CommServe", name)
            return
        logger.info("Agents: {}", ", ".join(self.cv.clients.agents(client_id)) or "none")
        for instance in self.cv.instances.list(client_id):
            logger.info(
                "Instance: {} / {} (id {})",
                instance.get("appName"),
                instance.get("instanceName"),
                instance.get("instanceId"),
            )
            if instance.get("appName") != "SQL Server":
                continue
            mssql = self.cv.instances.get(instance["instanceId"]).get("mssqlInstance", {})
            logger.info(
                "  not-ready reason: {!r} (Commvault does not report the login it was given)",
                mssql.get("notReadyReason"),
            )
            known = self.sql_known_databases(instance["instanceId"])
            logger.info(
                "Commvault knows {} databases on it: {}",
                len(known),
                ", ".join(f"{d.get('dbName')}(bkpTime={d.get('bkpTime')})" for d in known)
                or "none",
            )
        for entity in self.cv.subclients.list(client_id):
            logger.info(
                "Subclient: {} / {} / {} (id {})",
                entity.get("appName"),
                entity.get("instanceName"),
                entity.get("subclientName"),
                entity.get("subclientId"),
            )

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
        for job in self.cv.jobs.active():
            if job.raw.get("subclient", {}).get("subclientId") in mine:
                self.job_action(job.job_id, "kill")
        for name, entity in subclients.items():
            if entity["clientId"] != self.client_id:
                continue  # goes with its client, below
            try:
                self.cv.subclients.delete(entity["subclientId"])
            except SDKError as exc:
                logger.error("Deleting subclient {} failed - {}", name, exc)
            else:
                logger.info("Deleted subclient {}", name)
        for name, client_id in self.lab_clients().items():
            try:
                self.cv.clients.delete(client_id)
            except SDKError as exc:
                logger.error("Deleting client {} failed - {}", name, exc)
            else:
                logger.info("Deleted client {}", name)
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


def sql_sa_password() -> str:
    """The lab SQL Server's sa password: generated once, kept in
    lab/.state (gitignored), never in the repository."""
    path = STATE_DIR / "sql-sa-password"
    if not path.is_file():
        STATE_DIR.mkdir(exist_ok=True)
        path.touch(mode=0o600)
        path.write_text(f"Lab-{secrets.token_urlsafe(18)}-9z\n")
    return path.read_text().strip()


def sql_databases(container: str) -> list[str]:
    """Every database SQL Server itself says exists in `container` -
    the independent count a backup validation has to be measured against."""
    result = podman(
        "exec", container, "sqlcmd", "-C", "-S", "localhost", "-U", "sa", "-P", sql_sa_password(),
        "-h", "-1", "-W", "-Q", "set nocount on; select name from sys.databases order by name",
    )  # fmt: skip
    if result.returncode != 0:
        raise SystemExit(f"Could not query SQL Server in {container}: {result.stderr.strip()}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def sql_exec(container: str, database: str, statement: str) -> None:
    podman(
        "exec", container, "sqlcmd", "-C", "-S", "localhost", "-U", "sa", "-P", sql_sa_password(),
        "-d", database, "-b", "-Q", statement,
    )  # fmt: skip


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
    global SQL_CLIENT
    parser = argparse.ArgumentParser(prog="cvlab", description=__doc__.split("\n")[0])
    parser.add_argument(
        "command",
        choices=[
            "preflight",
            "setup",
            "noise",
            "clients",
            "sql",
            "sql-report",
            "sql-backup",
            "sql-validate",
            "sql-add-db",
            "sql-offline",
            "sql-online",
            "teardown",
        ],
    )
    parser.add_argument("--commcell", default="cv-toaster")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--plan", default="Standard Plan")
    parser.add_argument("--count", type=int, default=3, help="clients: how many containers")
    parser.add_argument("--databases", type=int, default=10, help="sql: user databases to create")
    parser.add_argument(
        "--sql-client",
        default=SQL_CLIENT,
        help="sql-*: which SQL Server container/client to act on",
    )
    parser.add_argument("--database", help="sql-offline / sql-online: the database")
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=24,
        help="sql-validate: a backup older than this counts as stale",
    )
    parser.add_argument(
        "--levels",
        default="Full,Differential,Transaction_Log",
        help="sql-backup: backup levels to run, in order",
    )
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
    SQL_CLIENT = args.sql_client

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
            # The lab is the one place meant to change a CommServe - but
            # only the commands that need to get a client that can.
            allow_changes=args.command not in READ_ONLY_COMMANDS,
        ) as commvault:
            lab = Lab(commvault, args.plan)
            if args.command == "preflight":
                sys.exit(0 if lab.preflight() else 1)
            elif args.command == "setup":
                lab.setup()
            elif args.command == "clients":
                host = urllib.parse.urlsplit(settings.cv_base_url).hostname or ""
                lab.clients_up(args.count, socket.gethostbyname(host))
            elif args.command == "sql":
                host = urllib.parse.urlsplit(settings.cv_base_url).hostname or ""
                lab.sql_up(socket.gethostbyname(host), args.databases)
            elif args.command == "sql-report":
                lab.sql_report()
            elif args.command == "sql-validate":
                sys.exit(0 if lab.sql_validate(args.max_age_hours) else 1)
            elif args.command in ("sql-offline", "sql-online"):
                # A database that exists but cannot be read: how a
                # "partial" backup is staged. Purely a SQL Server change.
                if not args.database:
                    raise SystemExit("--database NAME is required")
                state = (
                    "OFFLINE WITH ROLLBACK IMMEDIATE" if args.command == "sql-offline" else "ONLINE"
                )
                sql_exec(SQL_CLIENT, "master", f"alter database [{args.database}] set {state}")
                logger.info("{} in {} is now {}", args.database, SQL_CLIENT, state.split()[0])
            elif args.command == "sql-add-db":
                existing = [n for n in sql_databases(SQL_CLIENT) if n.startswith("lab_db_")]
                name = f"lab_db_{len(existing) + 1:02d}"
                sql_exec(
                    SQL_CLIENT,
                    "master",
                    f"create database {name}; alter database {name} set recovery full;",
                )
                sql_exec(
                    SQL_CLIENT,
                    name,
                    "create table notes (id int identity primary key, line nvarchar(200), "
                    "at datetime2 default sysutcdatetime()); "
                    "insert notes (line) values ('created');",
                )
                logger.info("Created {} in {} - it has no backup yet", name, SQL_CLIENT)
            elif args.command == "sql-backup":
                lab.sql_backup([lv for lv in args.levels.split(",") if lv], args.wait)
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
