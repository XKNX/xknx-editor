---
name: fix-application-download
description: >-
  Compare an ETS group-monitor dump of a successful KNX application download
  with the image xknx-editor would write, and fix the download code until they
  match. Use when device programming fails, LoadCompleted or another load
  object enters ERROR, a download leaves a device partially loaded, or the
  user supplies an ETS telegram log, group monitor export, or .log.xml of a
  working programming.
---

# Fix an application download

ETS is the reference. A failed editor download does not say which byte the device rejected. Get a group-monitor XML of ETS programming the same device successfully, then make the editor send the same bytes.

Do not change download code before that dump has been compared. Do not invent the missing bytes from the editor log alone.

## What to ask for

Ask for all of these before editing:

1. The ETS group-monitor dump of a **successful** full application programming of this device, saved as XML. The file is a `CommunicationLog` whose `Telegram` elements carry `RawData`. A screenshot or the editor log is not a substitute.
2. The saved project (`.xknx`) that was programmed. Unsaved parameter edits are invisible to the compare.
3. The device individual address (`1.1.4`).
4. The editor failure line, when they have it (`object 3 entered ERROR state` and the address). It says which load finished last; it does not say which byte was wrong.

The ETS run must use the same parameters, group links, and application as the project. If ETS was programmed from a different configuration, say so and stop.

## Compare

From the repository root:

```bash
uv run python packages/download/tools/compare_download_capture.py \
  --ets /path/to/ets-group-monitor.xml \
  --project /path/to/project.xknx \
  --device 1.1.4
```

The catalog default is the editor's `catalog.xknxcatalog`. Pass `--catalog` when it lives elsewhere, and `--password` when the project is protected.

The script rebuilds every memory byte ETS wrote (later writes win; a read fills a byte ETS never wrote) and builds the image through the same `build_image` path the GUI uses: saved parameters, module instances, qualified com-object refs, flag overrides, and group links. It does not compare coupler filter tables or Data Secure tables.

Exit 0 and `RESULT match` means every byte ETS wrote is in the editor image and equal, the editor would not change a byte ETS read and left alone, and every task-segment application id matches what the editor sends. Anything else is a difference to fix.

Read [reference.md](reference.md) only for a hint the script actually prints.

## Fix

Classify the mask before editing code. The device's mask version is on the application (`MaskVersion`, `MV-0705`). Its `features` and `resources` are in `packages/download/src/xknxeditor/download/mask_resources.json`. The module comment in `packages/download/src/xknxeditor/download/resources.py` says what each flavour changes in the download: address space is where the resource sits, flavour is which realisation it is, and `PropertyMappedLsms` chooses property transport even when a `LoadControl_M112` memory address is also published.

- Dispatch on the feature or flavour the mask already has (`PropertyMappedLsms`, `AssociationTable_M112`, `GroupObjectTable_Bcu11`, `LoadControl_M112`, `AddressTable_SystemB`).
- A flavour that is already implemented is a regression test for that flavour. Add the device under the existing test, and stop.
- A flavour the bundled table does not list is a change to `packages/download/tools/gen_mask_resources.py`, regenerated from `knx_master.xml`. A `HawkConfigurationData` with `LegacyVersion` set is the old view of the same mask (MV-07B0's second block). The generator keeps the block that has no legacy version.
- `LegacyPatchManufacturerIdInTaskSegment` is an application option. Product XML addresses of a `Pointer` resource are the maximum layout.
- Do not add a manufacturer test or a new set of mask ids. That is the branch the flavour table replaces.

Change only the mismatch the script reports.

- `group_object_communication` or `communication_bit`: the group-object overlay. See reference.md.
- `task segment app … != editor`: `_task_segment` in `procedure.py`. See reference.md.
- `uncovered` or `would overwrite`: a segment the editor writes differently from ETS. Follow the address back to the load-control record printed above it (which LSM, which absolute segment) and then to the code that fills that segment.
- `other`: do not guess a flag or manufacturer fix. Diff those addresses against the ETS sequence and the segment they sit in.

Lock the corrected byte with a test next to the existing download tests (`packages/download/tests/`). Re-run the script until it prints `RESULT match`. A unit test is not a substitute for that.

Restart the editor before a device retry (`uv run python -m editor_gui.main`). A process started earlier is still on the old library. Ask for a full download. A failed `LoadCompleted` can leave the device partially loaded; a later successful ETS programming is what restores it, so the retry has to be a full download too.

Do not commit unless the user asks.

## How a failure is shaped

`object N entered ERROR state (expected LOADED)` is the first load whose completion the device rejected. Earlier loads in that run succeeded. N is the load-procedure index (LSM), not a group-object number. The script's load-control list is the ETS sequence for those same indexes: `UNLOAD`, `LOAD`, `ABS_DATA` / `ABS_TASK`, `COMPLETE`.

When the only image diffs are flag bytes, the parameter encoder is already right. The device checksums the segment it was given; a missing trailing CRC field is not the bug.

Address and association tables that ETS only reads are already correct on the device. A diff there matters when the editor would write a different byte (`would overwrite`).
