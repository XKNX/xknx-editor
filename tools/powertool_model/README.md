# PowerTool Device Model

`packages/vendor/src/xknxeditor/vendor/powertool/device-model.json.gz` contains
UTF-8 JSON in the editor's versioned format. It describes shared encoding layouts
and configuration decisions for all supported PowerTool families.

The format reorganizes information derived from PowerTool. It does not change the
origin of the underlying product facts or the device module payload. The module
is data for a device image; the editor does not execute it.

## Format 1

- `layouts`: shared node records containing storage positions, ordered parameter
  and object references, defaults, option encodings and placement constraints.
- `families`: database identities, product roots and node bindings. Each node
  binding has a `layout` index and optional `rules` mapping property names to
  decision references. Original type names identify serialized objects and remain
  stable across imports, saved configurations and role bindings.
- `modules`: shared device module records (`heap`, `init`, `destroy`, hexadecimal
  `data`). A family's `modules` maps function codes to indices in this table.
- `decisions`: three shared tables, described below.

`decisions.values` holds operands and results:

| Record | Meaning |
|---|---|
| `["literal", value]` | Exact scalar value, retaining its type |
| `["field", path]` | A configuration field relative to the current node |
| `["scaled", integer, path]` | Integer shifted left by the referenced field's value |

Paths start at `Application`, `Product`, `ParentParameter` or `ParentChannel`.
They traverse `Parameters`/`Channels` followed by an index, or `Application`, and
end at `ParameterValue`, `Exists`, `Visible`, `Shift` or `type`.
`["base", "Type"]` reads the declared object type.

`decisions.predicates` contains `truth` with one value index; `equal`, `different`,
`above`, `at_least`, `below` or `at_most` with two value indices; or `attached` with
a sibling device's type identity.

`decisions.branches` contains `[predicate_index, true_reference, false_reference]`.
A nonnegative reference selects another branch. A negative reference selects the
result at `values[-reference - 1]`. Only the selected branch is followed, so an
unavailable field in an unselected branch is never read. Branch order is part of
the behavior and must not be sorted or reduced merely because results coincide.

Changes must preserve complete PID images, option indices, typed defaults and
evaluation order. Run the PowerTool vendor tests; optional original image archives
are used when installed. UI wording belongs in the separate text map and gettext
catalogs. Device payload strings remain unchanged.
