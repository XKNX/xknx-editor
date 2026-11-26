"""Measure the browser editor and the memory used by a single application.

From the repository root (use --help on each subcommand for its options)::

    uv run python apps/editor-gui/scripts/measure_memory.py fixture --devices 2 --out fixture.knxproj
    uv run python apps/editor-gui/scripts/measure_memory.py run --knxproj fixture.knxproj --out cold.json
    uv run python apps/editor-gui/scripts/measure_memory.py run --knxproj fixture.knxproj --mode warm --out warm.json
    uv run python apps/editor-gui/scripts/measure_memory.py app
    uv run python apps/editor-gui/scripts/measure_memory.py verify --knxproj fixture.knxproj --before ../base --after .
    uv run python apps/editor-gui/scripts/measure_memory.py compare before.json after.json

The fixture and application probe default to the platform's editor data directory. Select another
with --catalog-dir. Fixture devices use distinct applications ranked by uncompressed XML size.
Repeat an ID in --apps to create several devices with that application. --knxproj uses an
existing archive. Pass its password through --password-file where needed.

Cold runs begin with an empty catalog and parse cache. Warm runs import and evaluate the same
archive once in a separate editor, then time opening the resulting .xknx in a fresh editor.
The phases sample editor RSS and the sum of RSS across it and its descendants, including parser
workers. Shared pages can be counted more than once and short peaks can fall between samples.
CPU seconds cover user and system time, including reaped children where supported. A browser
runs outside that tree. Use --browser with `uv run --with playwright==1.64.0` after running
`uv run --with playwright==1.64.0 playwright install chromium`.

--pi CORES:MiB simulates the resource budget on Linux and pins the editor with taskset. It
does not emulate Pi hardware. Add --hard-limit to cap memory with a systemd user scope.
Use --profile FILE with `uv run --with py-spy` for a flame graph of the measured launch.

The app probe reports fresh-process RSS before and after loading from the editor's parse cache.
Its deep-size estimates count distinct Python objects once, in the displayed part order.
String bytes are included in each part's total.

Use --snapshot on two runs to include correctness data for compare. Verify takes cold
snapshots from the two worktrees. It compares evaluated parameters, including those hidden
by the current configuration, group objects, planned memory bytes, and the uncompressed
contents of the export, decrypting its project folder when applicable. Snapshots record counts,
member names and content hashes so that parameter values and decrypted text stay in memory.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Generator, Sequence
from contextlib import AsyncExitStack, closing, contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from zipfile import ZipFile

import platformdirs
from memory_measurement.analysis import (
    archive_manifest,
    compare,
    digest,
    measure_cached_app,
)
from memory_measurement.editor import (
    APP_NAME,
    MIB,
    SAMPLE_INTERVAL,
    Recorder,
    RunOptions,
    call,
    devices,
    editor,
    export,
    render_table,
    write_json,
)

if TYPE_CHECKING:
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    from xknxeditor.catalog import CatalogService
    from xknxeditor.catalog.core.products import ProductSummary

CATALOG = "catalog.xknxcatalog"
SCRIPT = Path(__file__).resolve()


def snapshot_catalog(source: Path, dest: Path) -> Path:
    """Copy the catalog and the product archives it references into temporary storage."""
    src = source / CATALOG
    if not src.is_file():
        raise FileNotFoundError(f"No catalog at {src}")
    dest.mkdir(parents=True)
    db = dest / CATALOG
    store = dest / "knxprod"
    store.mkdir()
    with (
        closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)) as original,
        closing(sqlite3.connect(db)) as copy,
        copy,
    ):
        original.backup(copy)
        for (old,) in copy.execute(
            "SELECT DISTINCT knxprod_path FROM hardware_programs"
        ).fetchall():
            replacement = source / "knxprod" / Path(old).name
            if not replacement.is_file():
                replacement = Path(old)
            new = store / replacement.name
            if not new.exists():
                shutil.copy2(replacement, new)
            copy.execute(
                "UPDATE hardware_programs SET knxprod_path = ? WHERE knxprod_path = ?",
                (str(new), old),
            )
    if (source / "knx_master.xml").is_file():
        shutil.copy2(source / "knx_master.xml", dest / "knx_master.xml")
    return db


def select_products(
    catalog: CatalogService, app_ids: tuple[str, ...], count: int
) -> list[tuple[int, ProductSummary]]:
    products: dict[str, ProductSummary] = {}
    for product in catalog.list_products():
        if product.application_id is not None:
            products.setdefault(product.application_id, product)
    if missing := set(app_ids) - products.keys():
        raise ValueError(
            f"Applications without catalog products: {', '.join(sorted(missing))}"
        )

    ranked: list[tuple[int, ProductSummary]] = []
    for app_id in app_ids or tuple(products):
        product = products[app_id]
        source = catalog.get_program_source(product.hardware2program_ref_id)
        if source is None:
            raise ValueError(f"No product archive for {app_id}")
        path, manufacturer_id = source
        with ZipFile(path) as archive:
            try:
                size = archive.getinfo(f"{manufacturer_id}/{app_id}.xml").file_size
            except KeyError:
                if app_ids:
                    raise ValueError(f"No application XML for {app_id}") from None
                continue
        ranked.append((size, product))
    if app_ids:
        return ranked
    if count > len(ranked):
        raise ValueError(
            f"Requested {count} devices but the catalog has only {len(ranked)} distinct applications"
        )
    return sorted(ranked, key=lambda entry: (-entry[0], entry[1].application_id or ""))[
        :count
    ]


@contextmanager
def copied_catalog(source: Path, dest: Path) -> Generator[CatalogService]:
    from xknxeditor.catalog import CatalogService

    catalog = CatalogService(snapshot_catalog(source, dest))
    try:
        yield catalog
    finally:
        catalog.close()


async def fixture(args: argparse.Namespace, root: Path, password: str | None) -> None:
    if args.out.exists():
        raise FileExistsError(f"Output already exists: {args.out}")
    if args.knxproj is not None:
        archive_manifest(args.knxproj, password)
        shutil.copyfile(args.knxproj, args.out)
        print(f"Copied fixture to {args.out}")
        return

    options = RunOptions(Path(sys.executable))
    with copied_catalog(args.catalog_dir, root / "data" / APP_NAME) as catalog:
        selected = select_products(catalog, args.apps or (), args.devices or 1)
    async with editor(root, options) as (client, _):
        await call(client, "project_new", path=str(root / "fixture.xknx"))
        if password is not None:
            await client.call_tool("measurement_set_password", {"password": password})
        for size, product in selected:
            added = await call(
                client,
                "project_add_device",
                product_ref_id=product.product_ref_id,
                hardware2program_ref_id=product.hardware2program_ref_id,
            )
            print(
                f"{added.get('individual_address')}  {product.application_id}  {size / MIB:.2f} MiB XML"
            )
        await devices(client)
        await export(client, args.out)
    print(f"Wrote fixture to {args.out}")


async def read_parameters(
    client: Client[StreamableHttpTransport], device_apps: dict[int, bool]
) -> None:
    for node, has_app in device_apps.items():
        if has_app:
            await call(
                client, "project_list_parameters", node_id=node, include_hidden=True
            )


def items_digest(result: dict[str, Any]) -> dict[str, object]:
    """Compare every item field and the order of the returned rows."""
    canonical = json.dumps(
        result["items"], sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )
    return {"count": result["count"], "sha256": digest(canonical.encode("ascii"))}


async def collect_snapshot(
    client: Client[StreamableHttpTransport],
    device_apps: dict[int, bool],
    exported: Path,
    password: str | None,
) -> dict[str, object]:
    snapshots: dict[str, object] = {}
    for node, has_app in device_apps.items():
        if not has_app:
            snapshots[str(node)] = {"has_application": False}
            continue
        parameters = await call(
            client, "project_list_parameters", node_id=node, include_hidden=True
        )
        objects = await call(client, "project_list_com_objects", node_id=node)
        memory = await call(client, "project_memory_segments", node_id=node)
        for segment in memory["segments"]:
            segment["sha256"] = digest(bytes.fromhex(segment.pop("planned")))
        snapshots[str(node)] = {
            "parameters": items_digest(parameters),
            "com_objects": items_digest(objects),
            "memory": memory,
        }
    return {"devices": snapshots, "export": archive_manifest(exported, password)}


async def load_project(
    client: Client[StreamableHttpTransport],
    archive: Path,
    project: Path,
    password: str | None,
    *,
    warm: bool,
) -> dict[int, bool]:
    if warm:
        await call(client, "project_open", path=str(project), password=password)
    else:
        await call(
            client,
            "project_import_knxproj",
            source=str(archive),
            dest=str(project),
            password=password,
        )
    return await devices(client)


async def start_browser(stack: AsyncExitStack, web: str) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ValueError("--browser needs uv run --with playwright==1.64.0") from exc

    playwright = await stack.enter_async_context(async_playwright())
    browser = await playwright.chromium.launch(
        args=[
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
        ]
    )
    stack.push_async_callback(browser.close)
    page = await browser.new_page(viewport={"width": 1280, "height": 800})
    await page.goto(web)
    await page.wait_for_function("window.xknxWebStats?.frames > 0")


async def run_editor(
    root: Path,
    archive: Path,
    password: str | None,
    options: RunOptions,
    *,
    warm: bool,
) -> dict[str, object]:
    project = root / "project.xknx"
    if warm:
        async with editor(root, replace(options, profile=None)) as (client, _):
            device_apps = await load_project(
                client, archive, project, password, warm=False
            )
            await read_parameters(client, device_apps)
            await call(client, "project_close")

    recorder = Recorder(root / "editor.pid")
    recorder.pid_file.unlink(missing_ok=True)
    async with AsyncExitStack() as stack:
        with recorder.phase("start"):
            client, web = await stack.enter_async_context(editor(root, options))
            has_shell = any(
                tool.name == "shell_open" for tool in await client.list_tools()
            )
        if options.browser:
            with recorder.phase("browser"):
                await start_browser(stack, web)
        with recorder.phase("open" if warm else "import"):
            device_apps = await load_project(
                client, archive, project, password, warm=warm
            )
        with recorder.phase("parameters"):
            await read_parameters(client, device_apps)
        # A project of only generic devices has no application editor to open.
        first = next((node for node, has_app in device_apps.items() if has_app), None)
        if first is not None:
            with recorder.phase("open device editor"):
                if has_shell:
                    await client.call_tool(
                        "shell_open", {"key": f"panel:editor:device:{first}"}
                    )
                else:
                    await client.call_tool(
                        "measurement_open_device", {"node_id": first}
                    )
                await call(client, "project_status")
        exported = root / "export.knxproj"
        with recorder.phase("export"):
            await export(client, exported)
        with recorder.phase("idle"):
            await asyncio.sleep(options.idle)
        result: dict[str, object] = {
            "mode": "warm" if warm else "cold",
            "memory_use": options.memory_use,
            "pi": options.pi,
            "hard_limit": options.hard_limit,
            "browser": options.browser,
            "profiled": options.profile is not None,
            "sample_interval_s": SAMPLE_INTERVAL,
            "devices": len(device_apps),
            "devices_with_applications": sum(device_apps.values()),
            "phases": [asdict(phase) for phase in recorder.phases],
        }
        if options.snapshot:
            result["snapshot"] = await collect_snapshot(
                client, device_apps, exported, password
            )
    print(render_table(recorder.phases))
    return result


def worktree_python(path: Path, env_dir: Path) -> Path:
    if not (path / "apps" / "editor-gui" / "pyproject.toml").is_file():
        raise ValueError(f"Not an editor worktree: {path}")
    executable = subprocess.check_output(
        [
            "uv",
            "run",
            "--project",
            str(path),
            "--locked",
            "python",
            "-c",
            "import sys; print(sys.executable)",
        ],
        cwd=path,
        env={
            **{key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"},
            "UV_PROJECT_ENVIRONMENT": str(env_dir),
        },
        text=True,
    )
    return Path(executable.strip())


async def verify(args: argparse.Namespace, root: Path, password: str | None) -> int:
    output = args.out_dir or root
    output.mkdir(parents=True, exist_ok=True)
    paths = [output / "before.json", output / "after.json"]
    for checkout, path in zip((args.before, args.after), paths, strict=True):
        print(f"Measuring {checkout}", flush=True)
        python = worktree_python(checkout, root / f"{path.stem}-venv")
        options = RunOptions(python, args.memory_use, snapshot=True)
        result = await run_editor(
            root / path.stem, args.knxproj, password, options, warm=False
        )
        write_json(path, result)
    return compare(*paths)


def app_probe(args: argparse.Namespace, root: Path) -> None:
    from editor_gui.device_snapshot import code_fingerprint

    from xknxeditor.prod import parse_application_xml
    from xknxeditor.prod.parse_cache import set_tree_code

    cache = root / "parse-cache"
    with copied_catalog(args.catalog_dir, root / "catalog") as catalog:
        ((_, product),) = select_products(
            catalog, (args.app_id,) if args.app_id else (), 1
        )
        app_id = product.application_id
        assert app_id is not None
        xml = catalog.get_application_xml(product.hardware2program_ref_id)
        if xml is None:
            raise ValueError(f"No application XML for {app_id}")
        data, manufacturer = xml
        set_tree_code(code_fingerprint())
        parse_application_xml(data, manufacturer, cache_dir=cache, source=app_id)
    # Let the preparation process release its parsed objects before the fresh measurement.
    del data, xml
    gc.collect()
    result = cast(
        dict[str, Any],
        json.loads(
            subprocess.check_output(
                [sys.executable, str(SCRIPT), "_app", str(cache), app_id, manufacturer],
                text=True,
            )
        ),
    )
    if args.out is not None:
        write_json(args.out, result)
    print(
        f"{app_id}: {result['rss_after_bytes'] / MIB:.1f} MiB RSS after load "
        f"({result['rss_delta_bytes'] / MIB:+.1f} MiB in {result['load_s']:.2f} s)"
    )
    for part, row in result["deep_size"].items():
        print(
            f"  {part:<24} {row['bytes'] / MIB:7.2f} MiB ({row['string_bytes'] / MIB:.2f} MiB strings)"
        )


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number


def pi_budget(value: str) -> tuple[int, int]:
    try:
        cores, mib = value.split(":")
        return positive_int(cores), positive_int(mib)
    except (ValueError, argparse.ArgumentTypeError):
        raise argparse.ArgumentTypeError(
            "expected CORES:MiB, both positive integers"
        ) from None


def app_ids(value: str) -> tuple[str, ...]:
    ids = tuple(part.strip() for part in value.split(","))
    if not all(ids):
        raise argparse.ArgumentTypeError("expected non-empty application IDs")
    return ids


def absolute(value: str) -> Path:
    return Path(value).expanduser().resolve()


def arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    fixture_parser = commands.add_parser(
        "fixture", help="build a project archive or copy an existing one"
    )
    selection = fixture_parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--devices",
        type=positive_int,
        help="number of distinct applications (default: 1)",
    )
    selection.add_argument(
        "--apps", type=app_ids, help="comma-separated application IDs"
    )
    selection.add_argument("--knxproj", type=absolute, help="existing fixture to copy")
    fixture_parser.add_argument(
        "--out", type=absolute, required=True, help="fixture .knxproj to write"
    )
    run_parser = commands.add_parser("run", help="measure editor phases")
    run_parser.add_argument("--mode", choices=("cold", "warm"), default="cold")
    run_parser.add_argument(
        "--browser", action="store_true", help="attach Playwright Chromium"
    )
    run_parser.add_argument("--pi", type=pi_budget, metavar="CORES:MiB")
    run_parser.add_argument(
        "--hard-limit", action="store_true", help="enforce the --pi memory budget"
    )
    run_parser.add_argument(
        "--profile", type=absolute, metavar="FILE", help="write a py-spy flame graph"
    )
    run_parser.add_argument("--idle", type=float, default=2.0, metavar="SECONDS")
    run_parser.add_argument(
        "--snapshot", action="store_true", help="include correctness data for compare"
    )
    run_parser.add_argument(
        "--out", type=absolute, required=True, help="measurement JSON to write"
    )
    verify_parser = commands.add_parser(
        "verify", help="compare correctness across two worktrees"
    )
    verify_parser.add_argument("--before", type=absolute, required=True)
    verify_parser.add_argument("--after", type=absolute, required=True)
    verify_parser.add_argument(
        "--out-dir", type=absolute, help="keep before.json and after.json here"
    )
    for command in (run_parser, verify_parser):
        command.add_argument("--knxproj", type=absolute, required=True)
        command.add_argument(
            "--memory-use", choices=("low", "automatic", "high"), default="automatic"
        )
    for command in (fixture_parser, run_parser, verify_parser):
        command.add_argument("--password-file", type=absolute)
    app_parser = commands.add_parser(
        "app", help="measure a cached application in a fresh process"
    )
    app_parser.add_argument(
        "app_id", nargs="?", help="application ID (default: largest by XML bytes)"
    )
    app_parser.add_argument(
        "--out", type=absolute, help="application measurement JSON to write"
    )
    catalog_dir = Path(platformdirs.user_data_dir(APP_NAME)).resolve()
    for command in (fixture_parser, app_parser):
        command.add_argument("--catalog-dir", type=absolute, default=catalog_dir)
    compare_parser = commands.add_parser(
        "compare", help="compare two correctness snapshots"
    )
    compare_parser.add_argument("a", type=absolute)
    compare_parser.add_argument("b", type=absolute)
    args = parser.parse_args(argv)
    if args.command == "run":
        if args.hard_limit and args.pi is None:
            parser.error("--hard-limit requires --pi")
        if not 0 <= args.idle < float("inf"):
            parser.error("--idle needs a finite, non-negative number")
        inputs = {args.knxproj, args.password_file} - {None}
        if {args.out, args.profile} & inputs:
            parser.error("--out and --profile must differ from the input files")
        if args.out == args.profile:
            parser.error("--out and --profile must be different files")
    if (
        args.command == "verify"
        and args.out_dir is not None
        and {args.out_dir / "before.json", args.out_dir / "after.json"}
        & {args.knxproj, args.password_file}
    ):
        parser.error("the verification output files must differ from the input files")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = arguments(argv)
    if args.command == "compare":
        return compare(args.a, args.b)
    password_path = getattr(args, "password_file", None)
    password = (
        password_path.read_text(encoding="utf-8").rstrip("\r\n")
        if password_path
        else None
    )
    with tempfile.TemporaryDirectory(prefix="xknx-measure-") as temp:
        root = Path(temp)
        if args.command == "fixture":
            asyncio.run(fixture(args, root, password))
        elif args.command == "app":
            app_probe(args, root)
        elif args.command == "run":
            options = RunOptions(
                python=Path(sys.executable),
                memory_use=args.memory_use,
                pi=args.pi,
                hard_limit=args.hard_limit,
                profile=args.profile,
                browser=args.browser,
                idle=args.idle,
                snapshot=args.snapshot,
            )
            result = asyncio.run(
                run_editor(
                    root, args.knxproj, password, options, warm=args.mode == "warm"
                )
            )
            write_json(args.out, result)
        else:
            return asyncio.run(verify(args, root, password))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_app":
        cachedir, application, vendor = sys.argv[2:]
        print(json.dumps(measure_cached_app(Path(cachedir), application, vendor)))
    else:
        try:
            sys.exit(main())
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            sys.exit(str(error))
