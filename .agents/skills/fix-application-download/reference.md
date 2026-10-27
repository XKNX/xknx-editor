# Download mismatches seen against ETS

Use a section only when `compare_ets_download.py` reports that class. Re-run the script after the change.

## Group-object communication bit

Hint: `group_object_communication` or `communication_bit`.

The manufacturer seed leaves bit `0x04` set on every group-object slot. ETS clears it for slots that are not communicating. Two kinds of slot are easy to miss:

- A static `ComObject` with no dynamic `ComObjectRef` (unused channel, antitheft, alarm).
- A hole: a number below the highest object number that has no `ComObject` at all.

The table has one record per object number up to the highest, holes included; its count (first octet) is the highest number + 1. It starts at the table's `Offset` in its code segment, not at the segment start.

On the 4-octet table (`GroupObjectTable_M112`: count, 2-octet RAM flags pointer, records of value pointer, flags, size) the flag sits at `table + 5 + number * 4`. The diff is only bit `0x04` (`0xDF` vs `0xDB`, `0xCF` vs `0xCB`). `LoadCompleted` then checksums the wrong image and that LSM enters `ERROR`.

The overlay is `_group_communication_segments` / `_build_com_object_table` in `packages/download/src/xknxeditor/download/image.py`. A linked object keeps the bit.

`GroupObjectTable_Bcu10` / `GroupObjectTable_Bcu11` / `GroupObjectTable_Bcu1PL` (mask `MV-001x`, such as the Siemens N 561) and `GroupObjectTable_Bcu2` (`MV-002x`) are 3-octet records after the count and a 1-octet RAM flags pointer: value pointer, flags, type (`_short_com_object_table`). Every record gets a flag byte, also an object the device does not instantiate (its own flags) and a hole (none). On BCU1 bit 7 of each flag byte is set. BCU1 keeps the address, association, and group-object tables inside one EEPROM segment; laying a 4-octet record out from the start of that segment overwrites the application id and the table pointers. Which mask is which comes from the flavour in `resources.py`, not from a nibble of the mask id.

The count octet: a full download writes the product's octet, even when it differs from the count (inconsistent product data, e.g. `0xFE` on the Theben DM 4-2 T with 253 records). A partial download writes the count (`MemorySegment.partial_octets`), plus the RAM flags pointer and every record's value pointer from the seed.

## Task-segment manufacturer

Hint: `task segment app … != editor`.

`AllocAbsTask` carries a 5-octet application id: manufacturer (2), application number (2), version (1). ETS writes the product's own id. For the Berker 8014 (`M-0006`, application `0xE052`, version `0x12`, mask `MV-0701`) that is `0006e05212`.

`_task_segment` in `packages/download/src/xknxeditor/download/procedure.py` may replace the manufacturer with `0x0002` only when the application sets `LegacyPatchManufacturerIdInTaskSegment`. A memory-mapped mask is not a reason to patch. BIM M112 product procedures keep their own id.

The id is not inside the EEPROM image. It lives only in the task-segment load-control record, which is why a clean memory compare can still fail on the device.

## BIM M112 load control

Mask `MV-0701` (`LoadControl_M112`) uses one 11-octet memory record, usually at `0x0104`, not `PID_LOAD_STATE_CONTROL`. The script prints these as `memory LSM…`.

Octet 0 is `(lsm << 4) | opcode` (`1` load, `2` complete, `3` additional, `4` unload). An additional record inserts a reserved `0` after the segment type. Absolute data keeps the property-format length. Encoding is `memory_mapped_control` in `packages/download/src/xknxeditor/download/load_state.py`.

Status bytes come from the mask's `LoadStatus` resources (`packages/download/src/xknxeditor/download/resources.py`). `MV-0701` has no PEI `LoadStatus`. Skip that `AllocAbsTask`. Inventing the next address (for example `0xB6EE`) makes the device stop answering.

`object 3 entered ERROR` on this mask is application `LoadCompleted` (`LSM 3`), after the address table (`LSM 1`) and association table (`LSM 2`) have already loaded.

## Packed association table

Hint: `uncovered` bytes, and the same bytes are present in the editor image at the declared association-table address.

`AssociationTable_M112` with `PropertyMappedLsms` = 1 and an `AddressTable_Bcu1` in standard memory declares the two tables at their maximum size, contiguous. ETS writes only the used address table and places the association table immediately after it. The association segment still ends at the declared end. MV-0705 does this (`0x4000`, then the association table where the used address table ends). MV-0701 has the same table flavour with `PropertyMappedLsms` 0, so LSM 2 stays at the declared address (`0x41FF` on the Berker 8014). A gap between the declared segments is left where the product put it.

Load control on that property-mapped mask is `PID_LOAD_STATE_CONTROL`, including when `LoadControl_M112` also publishes `0x0104`. The compare script checks memory bytes, not which transport carried the load event. `memory_mapped_lsm()` staying set is what keeps the absolute-segment procedure.

The Merten 4806 procedure also lists an LSM 5 task segment and load after the restart. MV-0705 publishes no PEI LoadStatus, and ETS does not send them. Object 5 is `FirstAppObjectIdx`; a property write of PID 5 there is rejected with zero elements. Skip that LSM. A byte compare can still report `RESULT match`.

## What a matching Berker 8014 download contains

ETS, programming individual address `1.1.4` from tool `15.15.4`:

- Memory load control at `0x0104`, 11 octets.
- Unload, load, absolute segment, task segment, complete for LSM 1 at `0x4000` and LSM 2 at `0x41FF`.
- LSM 3: RAM segment, one-byte stack, then the EEPROM segment at `0x43FE` (checksum attribute `0x80`), its task segment `0006e05212`, then complete.
- The address table and association table were read and not rewritten.
- A property write and a restart follow the loads.

After the communication-bit and manufacturer fixes, the script reports `RESULT match` for that dump against the saved project.
