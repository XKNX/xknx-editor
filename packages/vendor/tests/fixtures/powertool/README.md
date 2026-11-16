# Local PowerTool reference fixtures

Optional local test data: ZIP archives of original-encoder captures, ignored by Git and
excluded from distribution packages. Tests skip only the cases whose archive is missing; a
present but corrupt archive fails. Set `XKNX_POWERTOOL_FIXTURES` to read the archives from
another directory. They hold no executable manufacturer code — the native `config.json` /
`host.json` are test inputs, and the expected `image.pid` files are original encoder outputs,
never Python-generated. These finite captures establish offline byte equality, not hardware
validation; read-only preflight stays available and full programming needs explicit user request.

- `reported-samples.zip` — the issue #278 blobs with the object-table readback and two Mono
  timer captures (before and after Python writeback).
- `original-images.zip` — 1,912 Mono `DeviceImage.CreateImage(true)` outputs: matrix,
  timer/rounding/mixed-channel and ordered-edit sequence cases, each with its JSON inputs.
- `original-projects.zip` — 77 serialized projects across all 13 databases plus 1,146 further
  6108 captures, with a shared metadata catalogue and a `modified-flags` capture.
- `family-images.zip` — 4,631 captures for the RTR, presence and solo profiles; one declarative
  schema, channels keyed by type.
- `family-sequences.zip` — 27 outputs after ordered edits through a saved project.
- `windows-captures.zip` — eleven Windows/.NET 3.5 outputs (2026-11-13); they differ from Mono
  only in object 0's property-record order.
- `windows-writeback.zip` — 22 Windows outputs accepting Python-written blobs.
- `all-profile-images.zip` — 1,735 originals (defaults, hardware layouts, subsystems, functions).
- `powerline-images.zip` — five Powerline cases (both association formats, repeater and ack bits).
  The image archives total 8,321 PID comparisons, not unique configurations.
- `all-profile-products.zip` — the `host-products.zip` application XML and the SmartTouch
  `.knxprod` XML, with a source/hash manifest.

Runtime `family-schema.json.gz` is a compact schema (parameter layout plus the B021 firmware
payload the watcher profiles need), not a capture archive; the payload is device firmware, no
desktop executable.
