# Local PowerTool reference fixtures

The ZIP archives in this directory are optional local test data. They are
ignored by Git and excluded from distribution packages. Tests skip only the cases
that require a missing archive; present but corrupt archives fail normally.
Set `XKNX_POWERTOOL_FIXTURES` to read the same archives from another directory.
The runtime schemas are separate from these original-encoder captures.

`original-images.zip` contains 1,912 complete PID outputs from the unchanged original
`DeviceImage.CreateImage(true)` encoder, executed locally on Mono:

- `fixtures/`: 1,146 matrix cases (585 for /01 and 561 for /02).
- `fixtures-extended/`: 754 timer, numeric rounding and mixed-channel cases.
- `sequences/`: 12 outputs after ordered parameter changes in an existing instance.

Each case contains the exact `image.pid` and its native `config.json`/`host.json`
inputs. The expected PID files are original outputs, never Python-generated
expectations. Tests compare the complete container, including masks and properties.
The archive contains no executable manufacturer code. Native configuration and host
JSON are test inputs, not proprietary saved-project blobs.

The full original matrix has not been replayed under Windows/.NET Framework;
the separate eleven-case Windows capture is described below. Native bridge output,
device capacity, hardware behavior and all parameter combinations remain unverified.
Full programming is enabled by explicit user request; these finite fixtures establish
offline byte equality, not hardware validation. Read-only preflight remains available.

`original-projects.zip` contains 77 unmodified original serialized projects covering
all 13 installed databases and 1,146 further 6108 captures with original PID outputs.
The shared metadata catalogue identifies database name, version, DBCode and product
type. The generic cases are default project captures: several older profiles start
without selected hardware or channels. They do not establish coverage of every
device/module combination. SmartTouch includes its nested channels.

Each matrix case compares conversion of the saved parameters to the complete PID,
using its independently captured host association inputs. Source blobs stay unchanged.
The Python reader never executes serialized .NET types. Unknown versions remain
inspectable with unverified labels; native conversion requires a captured database version and modeled state. Modified
original object flags are retained by native conversion.

One additional original `modified-flags` capture sets ReadEnable on the first object.
Inspection and native conversion preserve the flag.
Its capture record includes exact blob/PID and source-probe hashes.

`family-images.zip` adds 4,631 full original PID captures for ObjektRTR (243),
ObjektRTRunified (198), PraesenzStandard (164), PraesenzKomfort (2,801),
SoloRTR3 (775) and SoloRTR3oBE (450). Each has a native config and the original
edit request; the manifest records original PID hashes. Provenance includes the
host adapter inputs, the capture helper source and original assembly hashes.
The expectation bytes are unmodified original encoder output from Mono. Earlier
failed probe selections are retained outside this archive as diagnostic evidence.
The corrected capture identifies channels by type, since channel numbers repeat.
A single declarative schema serves these profiles; no per-device mapping is needed.

`family-sequences.zip` contains 27 further original outputs after ordered edits
continued through the original saved project, including no-op selection and function return.
Two parameter edits made unavailable by preceding changes are excluded and recorded
in the capture log. These are finite sequences, not proof of all change combinations.

`windows-captures.zip` is the unchanged archive from the successful Windows/.NET
Framework 3.5 run on 2026-11-13. It contains eleven full PID outputs, saved blobs,
context, channels, logs, environment details and original assembly hashes, with no
DLLs or EXEs. Three cases replay Mono inputs (SoloRTR3, SmartTouch, TasterBA1f);
eight cover priOn TFT layouts. Resources, masks and property values match the three
Mono originals. Raw PID files differ only in object 0 property-record order:
Windows emits 86,52,51,85; Mono emits 52,51,85,86. The files remain unmodified.
This archive retains its original Windows ZIP paths and UTF-8 BOM manifests.
All eleven complete PIDs are consumed by the Python tests with an explicit Windows
property order. Mono expectations retain their original order.

`windows-writeback.zip` contains 22 Windows outputs accepting Python-written blobs:
the eleven configurations above and one parameter edit each. The recovered archive
was verified against all 158 unchanged source files after the original ZIP omitted
one case on a VM shared drive. Tests compare complete PIDs from the input, original
reserialized project and Python rewrite. Interactive PowerTool GUI behavior remains
unverified. Like the other captures, this archive is optional and Git-ignored.

`all-profile-images.zip` adds 1,735 complete originals: 77 profile defaults, 138
hardware layouts, 274 subsystem selections and 1,246 function captures. It includes
saved blobs, original context and mutation requests. All full PIDs compare exactly.
These use diagnostic Main/PEI identities; actual hardware identity is separately
validated from catalog data. Failed original preview operations remain diagnostic
artifacts outside this archive and are not counted as captures.

`powerline-images.zip` adds five complete original PID cases: a default and four
hosted-link cases with both association formats and repeater states. They cover
acknowledgement bits and corresponding object flags. Windows Powerline serialization
and populated Windows association tables remain unverified. Together the image
archives provide 8,321 original PID comparisons, not 8,321 unique configurations.

`all-profile-products.zip` contains the existing `host-products.zip` application XML
and the supplied SmartTouch `.knxprod` XML, unchanged under separate prefixes. Its
manifest records every source and member hash. The host product subset has no
Hardware.xml, so it does not prove all hardware pairings. The packaging script and
the archive SHA-256 registry live with the development reference evidence.

Runtime `family-schema.json.gz` is a compact schema, not a capture archive. It includes
parameter layout data and the B021 firmware payload needed by the captured watcher
profiles. The payload is device firmware data; no desktop DLL or executable is
distributed or executed. Original firmware provenance remains in development evidence.
