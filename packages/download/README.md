# xknxeditor-download

**Program real KNX devices end-to-end.**

This package takes a parsed product database and an application's Load Procedure
and commissions a physical device over a live KNX bus - assembling the memory
image, driving each loadable part's Load State Machine, writing memory and
properties, laying down the group communication tables, and restarting the
device. It is a vendor-independent implementation derived from the KNX Standard
v3.0.0.

A full download run through the GUI has programmed physical devices, and the
System B group communication path has been checked against genuinely programmed
devices. Recent changes are additionally covered by an offline replay of a
reference programming session; treat anything not confirmed on a live device as
unverified. The package also programs a virgin device's individual address and
offers a read-only preflight to preview every change before writing.

Under the hood it interprets the Load Procedure and executes it over a running
[`xknx`](https://github.com/XKNX/xknx) connection. It builds on the sibling
packages for the data side (`xknxeditor-prod` parses the `.knxprod` and encodes
parameter values into the memory image) and on `xknx` for the runtime side
(KNXnet/IP transport, application layer services, point-to-point management
connection).

## Scope

- Full download over a point-to-point connection: unload, load, write image,
  load completed, restart, per loadable part.
- Property based Load State Machine control (writing load events to
  `PID_LOAD_STATE_CONTROL`), following KNX Standard v3.0.0, 3/5/2. This is the
  mechanism used for every mask, including the older BIM M112 masks
  (MV-0700/0701/0705/5705) whose master data still lists a `StandardMemory`
  Application Load Control: their product Load Procedures drive the state machine
  through `PID_LOAD_STATE_CONTROL`, and the memory location is legacy and unused.
- Chunked memory and property writes, with optional read-back verification and
  interface object location by type.
- Optional device mask guard (`expected_descriptor`): the device descriptor is
  read and checked before any write, refusing to program the wrong device.
- APDU length negotiation: `max_apdu_length` defaults to reading the device's
  maximum (Device Object PID 56) for larger, fewer telegrams; pass an integer to
  fix it.
- Master Reset via `LdCtrlMasterReset` (A_Restart with restart type 1, erase code
  and channel; KNX Standard v3.0.0, 3/3/7 section 3.4.2.2).
- Function Property command and state read via `LdCtrlInvokeFunctionProp` /
  `LdCtrlReadFunctionProp` (A_FunctionPropertyCommand / A_FunctionPropertyState_Read;
  3/3/7 section 3.4.7).

Not covered yet: clearing a line coupler filter table (`LdCtrlClearLCFilterTable`,
coupler-only and not yet validated against a router), and USB transport. Unsupported
Load Controls are reported via `UnsupportedProcedureError` rather than guessed (see
[Implementation gaps](#implementation-gaps-and-diagnostics)).

## KNX Data Secure (Tool Key)

For a secure device, pass a `DeviceSecurity(address, tool_key)` as the `security`
argument to `download`/`preflight`. The whole session is then KNX Data Secure
protected with the device's Tool Key (the standard mode for device programming), the same
way a plain download runs - the programmer is unaware of it.

- The CCM construction (B0/Ctr0, the `A`/`P` split per S-AL service, the AES-CBC-MAC
  and AES-CTR steps) follows KNX Standard v3.0.0, 3/3/7 section 5 and is verified
  byte-for-byte against the worked examples in 3/3/7 Annex C (C.1.1-C.1.4).
- Before the first secured frame the session is synchronised with an S-A_Sync
  exchange (`DM_SecureSync`, 3/5/2), learning the device's Sequence Numbers.
- Frames are secured on the CEMI path (`xknx.cemi_handler.data_secure`), after the
  transport layer has assigned the connection-oriented sequence number that the B0
  block binds - the same hook xknx uses for group Data Secure.

The Tool Key can be supplied directly (`DeviceSecurity(address, tool_key)`) or read
from a KNX keyring: `load_device_security(path, password, address)` loads and
decrypts a `.knxkeys` file and returns the `DeviceSecurity` for the device, and
`device_security_from_keyring(keyring, address)` does the same from an already
loaded keyring. Decryption uses xknx's keyring loader. Wiring this into the GUI is
not part of this package yet.

## Group communication tables

A full, application, or group-communication download writes the three tables that link a
device's group objects to group addresses. Pass a `GroupCommunication`
(the device's own address plus its `GroupObjectLink`s) to `download`/`build_image`.
Two device models are handled, detected automatically from the application:

- **memory-mapped** (masks MV-0701/MV-0705): the address, association and com
  object tables sit at fixed segment addresses; a 1-octet count leads the address
  and association tables, and the address table starts with the device's own
  individual address.
- **System B** (mask MV-07B0): the tables live in relative memory addressed
  through each object's table reference. The formats differ (2-octet counts, no
  leading device address, a group object table covering every object number), and
  the master procedure supplies their load transactions (see
  `group_communication.py`).

The com object (group object) table carries only the objects the device actually
**instantiated** — its group object tree — not every object the parameter-driven UI
happens to show. When the device was imported from a project, that instantiated set
is authoritative: the download recomputes each of those objects' flags and leaves the
rest at their manufacturer seed (toggling only the communication bit). This matches
genuine implementations, which follow the group object's `Active` flag. It matters for products
with channel modes: e.g. a 4-channel dimmer configured as "2x Tunable White" does not
carry the individual per-channel objects that the raw parameter defaults would
otherwise activate. A device configured
from scratch (no saved instances) falls back to the parameter-visible set.

## Preview before writing

`preflight(...)` performs the whole download read-only: it reads the device's
current bytes at every location a write would target and returns the diff without
changing anything (and runs the application-fingerprint compare as a gate). Run it
before any real download to confirm the change set. The System B group
communication path is implemented end-to-end, but preflight still lets you
review any device before writing.

The GUI builds a **Test Programming** action directly on top of `preflight`:
it reports per memory segment and property whether the image this library would
generate matches what is already programmed on the device (so a programming run
cannot be made to write a wrongly generated image), and can export the current
and planned bytes of every location as a report. `download(...)` also accepts a
`progress(done, total)` callback so the GUI can show a load progress bar.

## Implementation gaps and diagnostics

A Load Control the interpreter does not execute never fails silently. The runner
raises `UnsupportedProcedureError` with a message that names the KNX Standard
service the control maps to, its position in the procedure and the target, so a
bug report shows immediately what is missing. The read-only `preflight` logs the
same information; unsupported semantics also fail its validation. The registry of known-but-unimplemented
controls (with their standard mapping) lives in `gaps.py`; controls that legitimately
have nothing to write are listed there too so the preview does not flag them.

Controls are filtered by `AppliesTo` before execution, and `OnError` handlers are
honoured: a matching handler continues only when it declares `Ignore=true`,
otherwise the failure propagates (with the application's `MessageRef` text when
available). Malformed responses and transport failures remain fatal. These rules
also apply to read-only preflight.

The runner and preflight also log their start (target, in-scope control count) and
every unsupported control through the `xknxeditor.download.procedure` logger.

## Usage

```python
from xknx import XKNX
from xknx.io import ConnectionConfig, ConnectionType
from xknxeditor.prod import load
from xknxeditor.download import download

registry = load("device.knxprod")
application = next(iter(registry.applications.values()))

xknx = XKNX(
    connection_config=ConnectionConfig(connection_type=ConnectionType.TUNNELING)
)
async with xknx:
    await download(
        xknx,
        "1.1.5",
        application,
        master=registry.master,  # needed for default/merged procedure styles
        parameter_values={"P-1_R-1": "1"},
    )
```

Pass `master` (`registry.master`) so the Load Procedure can be resolved: for
`DefaultProcedure`/`MergedProcedure` applications the mask version's default
procedure is merged with the application's fragments; `ProductProcedure`
applications carry the full procedure and work without it. For a partial
download pass `scope=DownloadScope.PARAMETERS`,
`DownloadScope.GROUP_COMMUNICATION`, or
`DownloadScope.PARAMETERS_AND_GROUP_COMMUNICATION` for both.

The device must already carry the target individual address; program a virgin
device's individual address first via `program_individual_address`.

## Public API

- `download(xknx, individual_address, application, *, master, device, image, group_communication, scope, parameter_values, max_apdu_length, expected_descriptor, progress)`
- `program_individual_address(xknx, individual_address, *, serial_number)`
- `preflight(...)` → `PreflightReport` (same arguments; read-only)
- `build_image(application, *, ui, device, parameter_values, group_communication)` → `DownloadImage`
- `GroupCommunication`, `GroupObjectLink`, `DownloadScope`
- `DeviceProgrammer`, `LoadProcedureRunner`
- `LoadState`, `LoadEvent`
- Errors: `DownloadError`, `LoadStateError`, `VerificationError`,
  `UnsupportedProcedureError`, `ImageError`
