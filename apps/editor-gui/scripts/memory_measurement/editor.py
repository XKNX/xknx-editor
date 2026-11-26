"""Launch, drive and sample an isolated editor process."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncGenerator, Generator, Sequence
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import psutil

if TYPE_CHECKING:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

APP_NAME = "xknx-editor"
MIB = 1024**2
SAMPLE_INTERVAL = 0.05
TIMEOUT = 300


@dataclass(frozen=True)
class Sample:
    main_rss: int
    tree_rss: int
    main_cpu: float
    tree_cpu: float


@dataclass(frozen=True)
class Phase:
    name: str
    wall_s: float
    main_cpu_s: float
    tree_cpu_s: float
    main_peak_bytes: int
    main_end_bytes: int
    tree_peak_bytes: int
    tree_end_bytes: int


def summarize_phase(name: str, wall_s: float, samples: Sequence[Sample]) -> Phase:
    """Reduce one phase's samples, including its boundary samples, to one row."""
    start, end = samples[0], samples[-1]
    return Phase(
        name,
        wall_s,
        max(sample.main_cpu for sample in samples) - start.main_cpu,
        max(sample.tree_cpu for sample in samples) - start.tree_cpu,
        max(sample.main_rss for sample in samples),
        end.main_rss,
        max(sample.tree_rss for sample in samples),
        end.tree_rss,
    )


def render_table(phases: Sequence[Phase]) -> str:
    header = f"{'Phase':<20} {'Wall s':>8} {'CPU s main/tree':>17} {'Main MiB peak/end':>19} {'Tree MiB peak/end':>19}"
    lines = [header]
    for phase in phases:
        cpu = f"{phase.main_cpu_s:.2f}/{phase.tree_cpu_s:.2f}"
        main = f"{phase.main_peak_bytes / MIB:.1f}/{phase.main_end_bytes / MIB:.1f}"
        tree = f"{phase.tree_peak_bytes / MIB:.1f}/{phase.tree_end_bytes / MIB:.1f}"
        lines.append(
            f"{phase.name:<20} {phase.wall_s:8.2f} {cpu:>17} {main:>19} {tree:>19}"
        )
    return "\n".join(lines)


def tree_sample(root: psutil.Process) -> Sample:
    main_rss = root.memory_info().rss
    times = root.cpu_times()
    main_cpu = times.user + times.system
    rss = main_rss
    cpu = main_cpu + times.children_user + times.children_system
    for child in root.children(recursive=True):
        with suppress(psutil.Error):
            child_rss = child.memory_info().rss
            times = child.cpu_times()
            rss += child_rss
            cpu += (
                times.user + times.system + times.children_user + times.children_system
            )
    return Sample(main_rss, rss, main_cpu, cpu)


class Recorder:
    def __init__(self, pid_file: Path) -> None:
        self.pid_file = pid_file
        self.process: psutil.Process | None = None
        self.phases: list[Phase] = []

    def sample(self) -> Sample:
        try:
            if self.process is None:
                self.process = psutil.Process(
                    int(self.pid_file.read_text(encoding="ascii"))
                )
            return tree_sample(self.process)
        except (FileNotFoundError, ValueError, psutil.Error):
            return Sample(0, 0, 0.0, 0.0)

    @contextmanager
    def phase(self, name: str) -> Generator[None]:
        start = time.monotonic()
        samples = [self.sample()]
        stop = threading.Event()

        def sample_periodically() -> None:
            while not stop.wait(SAMPLE_INTERVAL):
                samples.append(self.sample())

        thread = threading.Thread(target=sample_periodically)
        thread.start()
        try:
            yield
        finally:
            duration = time.monotonic() - start
            stop.set()
            thread.join()
            samples.append(self.sample())
            self.phases.append(summarize_phase(name, duration, samples))


def isolated_env(root: Path, token: str) -> dict[str, str]:
    """Point the editor at private settings, data, log and temporary directories."""
    env = os.environ.copy()
    for key in (
        "PYTHONPATH",
        "PYTHONHOME",
        "XKNX_PROJECT_PASSWORD",
        "XKNX_WEB_PASSWORD",
    ):
        env.pop(key, None)
    return {
        **env,
        "HOME": str(root / "home"),
        "USERPROFILE": str(root / "home"),
        "APPDATA": str(root / "appdata"),
        "LOCALAPPDATA": str(root / "localappdata"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_STATE_HOME": str(root / "state"),
        "TMPDIR": str(root / "tmp"),
        "TMP": str(root / "tmp"),
        "TEMP": str(root / "tmp"),
        "XKNX_MCP_AUTH_TOKEN": token,
    }


def free_ports() -> tuple[int, int]:
    with socket.socket() as web, socket.socket() as mcp:
        for probe in (web, mcp):
            probe.bind(("127.0.0.1", 0))
        return web.getsockname()[1], mcp.getsockname()[1]


def stop_editor(app: subprocess.Popen[bytes], pid_file: Path, profiled: bool) -> None:
    # End the editor first so py-spy can write its profile before the process group is torn down.
    if profiled and pid_file.exists() and app.poll() is None:
        with suppress(psutil.Error, subprocess.TimeoutExpired):
            psutil.Process(int(pid_file.read_text(encoding="ascii"))).terminate()
            app.wait(10)
            profiled = False
    if os.name == "posix":
        with suppress(ProcessLookupError):
            os.killpg(app.pid, signal.SIGTERM)
    elif app.poll() is None:
        app.terminate()
    try:
        code = app.wait(10)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            with suppress(ProcessLookupError):
                os.killpg(app.pid, signal.SIGKILL)
        else:
            app.kill()
        code = app.wait()
    if profiled and code != 0:
        raise RuntimeError(f"py-spy exited with status {code}")


@dataclass(frozen=True)
class RunOptions:
    python: Path
    memory_use: str = "automatic"
    pi: tuple[int, int] | None = None
    hard_limit: bool = False
    profile: Path | None = None
    browser: bool = False
    idle: float = 2.0
    snapshot: bool = False


def limit_command(command: list[str], options: RunOptions) -> list[str]:
    if options.pi is None:
        return command
    cores, mib = options.pi
    if sys.platform != "linux" or not shutil.which("taskset"):
        raise ValueError("--pi needs Linux and taskset")
    cpus = sorted(psutil.Process().cpu_affinity())
    if cores > len(cpus):
        raise ValueError(
            f"--pi requested {cores} CPUs, but affinity allows only {len(cpus)}"
        )
    command = ["taskset", "-c", ",".join(map(str, cpus[:cores])), *command]
    if options.hard_limit:
        if not shutil.which("systemd-run"):
            raise ValueError(
                "--hard-limit needs systemd-run and a running user manager"
            )
        command = [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "-p",
            f"MemoryMax={mib}M",
            "-p",
            "MemorySwapMax=0",
            *command,
        ]
    return command


async def wait_port(app: subprocess.Popen[bytes], port: int) -> None:
    async with asyncio.timeout(TIMEOUT):
        while app.poll() is None:
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                await asyncio.sleep(0.1)
            else:
                writer.close()
                await writer.wait_closed()
                return
    raise RuntimeError(
        f"Editor exited with status {app.returncode} before port {port} opened"
    )


@asynccontextmanager
async def editor(
    root: Path, options: RunOptions
) -> AsyncGenerator[tuple[Client[StreamableHttpTransport], str]]:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    data = root / "data" / APP_NAME
    data.mkdir(parents=True, exist_ok=True)
    for name in ("home", "tmp"):
        (root / name).mkdir(exist_ok=True)
    write_json(
        data / "app.json",
        {
            "locale": "en",
            "memory_use": options.memory_use,
            "update_check_enabled": False,
        },
    )
    pid_file = root / "editor.pid"
    pid_file.unlink(missing_ok=True)
    token = secrets.token_urlsafe(24)
    web_port, mcp_port = free_ports()
    cores, mib = options.pi or (0, 0)
    command = [
        str(options.python),
        "-u",
        str(Path(__file__).with_name("launch.py")),
        str(root),
        str(cores),
        str(mib * MIB),
        f"--web=127.0.0.1:{web_port}",
        "--web-no-browser",
        "--web-no-tray",
        "--new-instance",
        "--no-connect",
        f"--mcp=127.0.0.1:{mcp_port}",
    ]
    profile = options.profile
    if profile is not None:
        spy = shutil.which("py-spy")
        if spy is None:
            raise ValueError("--profile needs py-spy (use uv run --with py-spy)")
        command = [spy, "record", "--subprocesses", "-o", str(profile), "--", *command]
    command = limit_command(command, options)
    log = root / "editor.log"
    with log.open("wb") as stream:
        app = subprocess.Popen(
            command,
            cwd=root,
            env=isolated_env(root, token),
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=os.name == "posix",
        )
        try:
            await asyncio.gather(wait_port(app, mcp_port), wait_port(app, web_port))
            transport = StreamableHttpTransport(f"http://127.0.0.1:{mcp_port}/mcp")
            async with Client(transport, auth=token, timeout=TIMEOUT + 30) as client:
                yield client, f"http://127.0.0.1:{web_port}/"
            if app.poll() is not None:
                raise RuntimeError(f"Editor exited with status {app.returncode}")
        except Exception:
            with log.open("rb") as errors:
                errors.seek(max(0, log.stat().st_size - 8192))
                print(errors.read().decode(errors="replace"), file=sys.stderr)
            raise
        finally:
            stop_editor(app, pid_file, profile is not None)
    if profile is not None and not profile.is_file():
        raise RuntimeError("py-spy did not write the profile")


async def call(
    client: Client[StreamableHttpTransport], tool: str, **params: object
) -> dict[str, Any]:
    result = (await client.call_tool(tool, params)).data
    if not isinstance(result, dict):
        raise ValueError(f"{tool} did not return an object")
    return cast(dict[str, Any], result)


async def devices(client: Client[StreamableHttpTransport]) -> dict[int, bool]:
    async with asyncio.timeout(TIMEOUT):
        while (state := await call(client, "measurement_devices"))["loading"]:
            await asyncio.sleep(0.1)
    if state["missing"]:
        raise ValueError(f"Devices with missing product data: {state['missing']}")
    return {int(node): has_app for node, has_app in state["devices"].items()}


async def export(client: Client[StreamableHttpTransport], dest: Path) -> None:
    status = await call(client, "project_export_knxproj", dest=str(dest))
    if status["status"] != "exported":
        raise ValueError(
            f"Export incomplete: {status.get('warning', status['status'])}"
        )


def write_json(path: Path, data: object) -> None:
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
