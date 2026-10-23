"""Create a sample .xknx project with a handful of catalog devices."""

from dataclasses import dataclass
from pathlib import Path

from editor_gui.device import Device as _Device
from xknxeditor.catalog import CatalogService
from xknxeditor.proj import ProjectService

_INSTALLATION = 0


@dataclass
class DemoDevice:
    application_id: str
    name: str
    individual_address: str


DEMO_DEVICES = [
    DemoDevice("M-0001_A-9082-02-0908", "Shutter Actuator 4x", "1.1.1"),
    DemoDevice("M-0001_A-9801-83-D585", "Roller Shutter Switch 4x", "1.1.2"),
    DemoDevice("M-0001_A-980D-03-EF67", "Binary Input 2x", "1.1.3"),
    DemoDevice("M-0008_A-7072-21-5CC3-O000A", "Button Interface 2-gang", "1.1.4"),
    DemoDevice("M-0001_A-0003-32-73C9", "Line Coupler", "1.1.5"),
]


def generate_demo(output_path: Path, catalog_path: Path) -> None:
    if output_path.exists():
        output_path.unlink()

    if not catalog_path.exists():
        print(f"Error: catalog not found at {catalog_path}")
        print("Run 'uv run generate-catalog' first")
        return

    catalog = CatalogService(catalog_path)
    try:
        products = {
            p.application_id: p for p in catalog.list_products() if p.application_id
        }

        svc = ProjectService()
        pid = svc.create(output_path, "P-DEMO")

        # The baseline already seeds the 1.1 TP line where end devices belong; reuse it.
        segment_id = _tp_segment(svc, pid)
        print("Using seeded Area 1 / Line 1.1 (TP) for demo devices")

        for demo in DEMO_DEVICES:
            product = products.get(demo.application_id)
            app = catalog.get_application(demo.application_id)
            if product is None or app is None:
                print(f"Warning: {demo.application_id} not in catalog, skipping")
                continue
            octet = int(demo.individual_address.split(".")[2])
            init_device = _Device(
                node_id=0, name=demo.name, app=app, individual_address=""
            )
            module_instances = init_device.get_module_instances()
            svc.add_device(
                pid,
                segment_id,
                product.product_ref_id,
                address=octet,
                name=demo.name,
                hardware2program_ref_id=product.hardware2program_ref_id,
                module_instances=module_instances if module_instances else None,
            )
            print(f"Added: {demo.name} ({demo.individual_address})")

        svc.close(pid)
    finally:
        catalog.close()
    print(f"\nDemo project saved to: {output_path}")


def _tp_segment(svc: ProjectService, pid: str) -> int:
    """The seeded 1.1 TP line's segment (where end devices belong)."""
    for area in svc.topology(pid, _INSTALLATION).areas:
        for line in area.lines:
            for segment in line.segments:
                if segment.medium_type == "MT-0":
                    return segment.id
    raise RuntimeError("no TP line in seeded topology")


def main() -> None:
    from editor_gui.settings import config_dir

    base_path = Path(__file__).parent.parent.parent.parent
    output_path = base_path / "demo.xknx"
    catalog_path = config_dir() / "catalog.xknxcatalog"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    generate_demo(output_path, catalog_path)


if __name__ == "__main__":
    main()
