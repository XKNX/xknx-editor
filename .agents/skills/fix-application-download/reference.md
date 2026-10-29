# Download mismatches seen against ETS

Use a section only when `compare_download_capture.py` reports that class. Re-run the script after the change.

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

## Memory services

Hint: `MemoryExtendedWrite` / `MemoryExtendedRead` in the recording, or a memory range above 64 KiB (a System B relative segment whose `PID_TABLE_REFERENCE` is high, e.g. Zennio).

The application decides, not the mask: `Options/@SupportsExtendedMemoryServices="true"` or a secure application (`IsSecureEnabled`) means the device takes A_MemoryExtended_Read/Write (24-bit address, 8-bit count, 5 octets overhead, up to 255 octets). `memory_services` in `procedure.py` reads that; `DeviceProgrammer.extended_memory` uses it.

- Writes always use the extended service then. Reads only when the APDU is longer than 15 octets or the range ends above 64 KiB; a short read below it stays A_Memory_Read.
- A return code from `0x80` on is an error. Code `1` carries a CRC16 (`segment_crc`: CCITT, the augmented form, over count, 3-octet address and data), checked when the write is verified. Extended writes are never read back.
- Without the extended services: A_Memory below 64 KiB, A_UserMemory above (20 bits).
- Zennio applications (`M-0071`) fall back to the standard services for the rest of the connection when an extended service fails (`extended_memory_fallback`).
- MDT presence detectors (`M-0083`, application numbers 100–103, 106, 107) take at most 12 octets per standard (and user) memory service (`MemoryServices.max_count`).

## What a matching Berker 8014 download contains

ETS, programming individual address `1.1.4` from tool `15.15.4`:

- Memory load control at `0x0104`, 11 octets.
- Unload, load, absolute segment, task segment, complete for LSM 1 at `0x4000` and LSM 2 at `0x41FF`.
- LSM 3: RAM segment, one-byte stack, then the EEPROM segment at `0x43FE` (checksum attribute `0x80`), its task segment `0006e05212`, then complete.
- The address table and association table were read and not rewritten.
- A property write and a restart follow the loads.

After the communication-bit and manufacturer fixes, the script reports `RESULT match` for that dump against the saved project.

## Segments and LSM 5

Hint: a RAM segment written, a missing `0x46C0`-style range in a partial download, or an LSM 5 event the device rejects.

- An `AbsSegment` writes its image data only into EEPROM (`MemType & 3 == 3`). RAM (`1`, `2`) and other types are only allocated, in a full download as in a partial one (where the allocation becomes a `WriteMem`).
- Every declared code segment is an image segment, also one with nothing to write (zero mask, no parameters): a `WriteMem` over it writes nothing.
- `LdCtrlLoad` and `LdCtrlTaskSegment` of LSM 5 are dropped unless the mask has a `GroupObjectTableLoadControl` (only System B has one).
- A control whose object index the mask does not list has no type; a partial download keeps it as it is.

## System 300

Hint: mask `MV-0300…`, Siemens HVAC (`M-00FD`) and similar.

- The configuration is the one of the device's management descriptor (PID 72), e.g. `MV-0300-01000001000000000000`. An application naming plain `MV-0300` checks the descriptor with its first `LdCtrlCompareProp`; `device_mask_version` (`merge.py`) takes it from there.
- `GroupObjectTable_System300` (descriptor `01000001…`): the tables are properties (`tables_system300.py`). OT1 PID 23 holds the group addresses (2 octets each, ascending, no count). OT2 PID 23 holds the links (`[address index + 1][object number]`, 2 or 4 octets per element as the device describes the property). OT9 PID 51 holds 6 octets per object `1..highest`, octet 1 the flags without bit 5, the rest from parameters. The product's `WriteProp` controls (`Count="0"`) get each table's element count, and an empty table is not written. PID 52 is written only with parameters, and a download without parameters leaves PID 51's other octets to the device.
- The memory configuration (descriptor `01000000…`): `LsmIdx` n is object index n, PID 5. The table segments are RAM and are only allocated; the EEPROM segment `256..278` reaches the group address table's count octet at `278` (1 + group addresses), which is all that is written of the tables.

## BCU1 and BCU2 group tables

Hint: mask `MV-001x` / `MV-002x`, an association table that differs in its count or order, a product `WriteMem` at `0x0111`, or a BCU2 application with `DynamicTableManagement`.

- The address table is at `0x0116` (count 1 + n, then the own address and the group addresses). `0x0117`/`0x0118` are left to the image.
- The association list runs in object number order, each object's links in their order. It starts with one reserved slot per group object (`association_slots` in `tables.py`). Slot i is object i's sending link, or `FE i` when the object sends on none. A second sending link of an object counts as receiving. While the list is longer than the slots, an unused slot takes the first later link whose group address is not the one an object sends on from its own slot. BCU2 skips the slots for an application with `EasyCtrlModeModeStyleEmptyGroupComTables` and no active object.
- BCU1: with dynamic tables the association table directly follows the address table. The check is against the summed declared sizes, (3 + 2n) + (1 + 2m) ≤ (3 + 2·MaxEntries) + (1 + 2·MaxEntries). `0x0111` (GrAssocTabPtr) is always the low octet of its address.
- BCU2 without dynamic tables: the declared place and the MaxEntries checks. `0x0111` is written when the declared table lies in `0x0100..0x01FF`; every `MV-002x` mask has memory `0x0100` (880 octets), so the image covers it.
- BCU2 with dynamic tables: the association table moves to `0x0116` + (3 + 2n) + 1. The 1 applies when the mask has `AllocExtraByte` and the procedure's LSM 1 `AbsSegment` has `SegFlags & 0x80`. The table still ends where the declared one ends. The LSM 1 `AbsSegment` is allocated at 3 + 2n (+1 with that flag). The LSM 2 `AbsSegment` and `TaskSegment` start at the moved table, and the segment keeps its end (`MovedTables`).
- `M-0004_A-9019-01` and `M-00A6_A-0004-10` are downloaded as without dynamic tables (`STATIC_GROUP_TABLES`).
- Seed octets of the declared table areas (from `0x0119`, and the association table) are not written; with BCU1 dynamic tables, from `0x0119` to the declared association table's end.
- Group object table: the count is written when the product data leaves it unset. A size without a type is type 0, `LegacyVarData` 15, and 15–50 octets are 21–56.
- With the group object table in the factory image, an easy-style application with no active object sets every object's communication bit.

## Module parameters with a BaseValue

A module parameter whose `BaseValue` names an argument takes that argument's value: in the UI, for a `choose`, and in the download. Its own value, default or stored, does not count. An argument's own `BaseValue` adds to its value.
