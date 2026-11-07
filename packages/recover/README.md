# xknxeditor-recover

**Reconstruct a KNX project by reading devices back from the bus.**

This package is the inverse of [`xknxeditor-download`](../download): instead of writing
an application into a device, it discovers devices on a line, identifies the
installed application, reads the configured memory and properties over a live KNX
bus, and decodes them back into project data - group addresses, communication-object
links and flags, and parameter values. Every bus access is read-only.

It is a vendor-independent implementation derived from the KNX Standard v3.0.0 and
builds on the sibling packages: `xknxeditor-download` for the point-to-point read
primitives and the group-communication table formats, `xknxeditor-prod` for the
application model and the parameter encoder used as a decode oracle, `xknxeditor-catalog`
for identifying products, and `xknxeditor-proj` for writing the result.

## What it does

- **Scan** an individual-address range: probe each address over one point-to-point
  connection, reading the device descriptor (mask version) and the application
  program id in a single session (avoiding a reconnect race that makes rapid scans
  flaky). Sequential, read-only.
- **Identify**: read PID_PROGRAM_VERSION (manufacturer, application number,
  version) and match it against the catalog by manufacturer + number + version +
  mask version. A device whose exact version is not in the catalog, or that matches
  more than one product, is flagged for confirmation. The read
  is confirmed by a second read so a transient wrong reply cannot mis-identify it.
- **Read group communication**: locate and read the address, association and group
  object tables (memory-mapped BCU/System 7 and System B realisations) and decode
  them into group addresses, links, and per-object flags. The sending object of each
  address is derived by convention (first association per object).
- **Recover parameters** (best effort): decode top-level static memory and property
  parameters, and module-instance parameters resolved against a dynamic UI seeded
  with the recovered structural parameters (so module instances match the device).
  Reliable types (integers, enumerations, text, DPT9/IEEE floats, dates, IPv4,
  RGB/RGBW colour, raw data) are decoded; non-injective encodings (HSV colour, a
  date without a stored year) are reported as unknown.
- **Device dossier**: read the descriptive Device Object properties (serial number,
  manufacturer, order info, hardware type) for display and disambiguation.
- **Verify** (read-only): re-encode the recovered group communication and diff it
  against the device via `xknxeditor-download`'s preflight; zero changed bytes means the
  reconstruction re-encodes exactly to what is on the device.
- **Validate**: cross-device checks flag group addresses with no sending object or
  more than one.
- **Snapshot**: export a JSON forensic record of the raw and decoded data.

## Not covered / limitations

- Memory reads adapt to devices that cap an A_Memory_Read reply below the negotiated
  APDU (a short reply is accepted and the read continues), but a device that refuses
  a read still fails that one device (the scan continues; the device is marked in
  error).
- Module-instance recovery depends on the application's structural parameters being
  decoded correctly first; deeply conditional modules, unions, and circular selectors
  are not guaranteed. Review a recovered project before relying on it.
- Wide System B association tables are read when the index counts require it.
- Room/building names, comments, secure keys, and historical runtime values are not
  stored on devices and cannot be recovered from the bus.
- Parameter round-trip verification is limited to the group-communication scope,
  because re-encoding parameters would drive the evaluator with values for
  possibly-inactive references.
- **Byte-exact fidelity assumes the device was last programmed by ETS 6.** The
  encoder mirrors the table/parameter byte layout a device last programmed by
  ETS 6 carries (the Hawk formatter). A device programmed by ETS 6 verifies to
  zero changed bytes; one programmed by an older version (or another tool) may
  show *structural* differences even for an identical configuration. There is no
  way to detect the programming tool from the device - KNX stores no
  programming-tool marker (only serial/manufacturer/order/hardware are
  readable) - so the verify result itself is the signal: table diffs of `0` mean the
  encoding matched. Note the distinct case of a byte diff that is only a *value*
  difference (structure matches, table diffs `0`): that is configuration drift - the
  device holds a different configuration than the reference project - not a tool or
  encoder issue.

## Usage

```python
from xknx import XKNX
from xknx.io import ConnectionConfig, ConnectionType
from xknxeditor.catalog import CatalogService
from xknxeditor.recover import (
    scan_bus,
    probe_and_identify,
    match_application,
    recover_device_at,
)

catalog = CatalogService("catalog.xknxcatalog")
xknx = XKNX(
    connection_config=ConnectionConfig(connection_type=ConnectionType.TUNNELING)
)
async with xknx:
    for device in await scan_bus(xknx, "1.1.1", "1.1.20"):
        _, app_id = await probe_and_identify(xknx, device.address)
        if app_id is None:
            continue  # unprogrammed
        products = match_application(catalog, app_id)
        if not products:
            continue  # not in catalog - fetch it, then retry
        application = catalog.get_application(products[0].application_id)
        recovered = await recover_device_at(xknx, device.address, application)
        print(recovered.address, len(recovered.links), "links")
```

## Public API

- `scan_bus`, `probe_device`, `probe_and_identify`, `iter_addresses`, `DiscoveredDevice`
- `read_application_id`, `parse_application_id`, `match_application`, `AppId`, `ProductLookup`
- `recover_device`, `recover_device_at`, `RecoveredDevice`, `com_object_ref_by_number`, `seed_dynamic_ui`
- `read_group_communication`, `read_parameter_memory`, `read_property_values`
- `recover_parameters`, `RecoveredParameters`
- `read_dossier`, `DeviceDossier`
- Table decoders: `decode_group_address_table(_b)`, `decode_association_table(_b)`,
  `decode_com_object_table`, `decode_group_object_table_b`, `DecodedLink`, `DecodedGroupObject`
- `verify_recovered`, `build_group_communication`
- `validate_group_communication`, `LinkWarning`
- `device_snapshot`, `snapshots_json`
- Errors: `RecoverError`, `TableDecodeError`
