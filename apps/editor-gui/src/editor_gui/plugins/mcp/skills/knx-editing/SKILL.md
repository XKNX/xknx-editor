---
name: knx-editing
description: Comprehensive KNX project editing, parameter configuration, group address linking, and commissioning playbook for AI agents.
---

# KNX Editing & Commissioning Playbook (`xknx-editor`)

This playbook provides actionable guidance for AI agents operating on KNX projects through the `xknx-editor` MCP interface.

---

## 1. Core Mental Model: Offline Project vs. Online Bus

KNX system integration with `xknx-editor` is split into two distinct operational layers:

```
┌────────────────────────────────────────────────────────┐
│             OFFLINE PROJECT (Safe & Reversible)        │
│  - SQLite Project Database                             │
│  - Fully transactional with undo/redo (project_undo)   │
│  - Zero bus traffic, no hardware connection required   │
│  - Tools: project_*, catalog_*                         │
└──────────────────────────┬─────────────────────────────┘
                           │ Commissioning (Download)
                           ▼
┌────────────────────────────────────────────────────────┐
│             ONLINE BUS (Live Hardware State)           │
│  - Requires active connection via IP router/interface  │
│  - Irreversible via undo (writes directly to flash/RAM)│
│  - Real-time bus telegrams and hardware status         │
│  - Tools: connection_*, monitor_*                      │
└────────────────────────────────────────────────────────┘
```

### Key Entities
- **Individual Address** (e.g. `1.1.20`): Physical topology address (`Area.Line.Device`). Uniquely identifies hardware.
- **Group Address (GA)** (e.g. `1/1/20` or `1/20`): Logical multicast signal channel. Defines who talks to whom.
- **Communication Object (Com-Object)**: Hardware/firmware data endpoint inside a device (e.g. "Switch Output A", "Status Output A").
- **Datapoint Type (DPT)**: Standardized data format (e.g. DPT 1.001 `DPT_Switch` = 1 bit, DPT 5.001 `DPT_Scaling` = 8 bit 0–100%, DPT 9.001 `DPT_Value_Temp` = 2-byte float).

---

## 2. Open-KNX-KB: Vendor-Neutral Knowledge Base ("Standard vor Willkür")

Before designing group addresses or configuring device parameters, consult the built-in **Open-KNX-KB** knowledge base:

```
┌────────────────────────────────────────────────────────┐
│             OPEN-KNX-KB KNOWLEDGE BASE                 │
│  - 01_domains: Lighting, Shading, HVAC, HEMS, Safety   │
│  - 02_functional_blocks: Standard KOs, DPTs, Flags     │
│  - 03_addressing_patterns: 3-Level standard GA offsets │
│  - 04_device_archetypes: DALI-2, Heating, Push buttons │
│  - Tools: kb_search, kb_get_entry, kb_lookup_dpt       │
│  - Resources: kb://manifest, kb://<category>/<file>   │
└────────────────────────────────────────────────────────┘
```

### Knowledge Base Best Practices:
1. **Search Before Guessing:**
   ```json
   kb_search(query="DALI Notlicht")
   kb_search(query="Heizungsaktor PWM", category="device_archetypes")
   ```
2. **Retrieve Full Functional Block Specification:**
   ```json
   kb_get_entry(entry_id="fb_switch_actuator")
   ```
   Inspect required KOs, direction (`write` vs `transmit`), flags (`['C', 'W']` vs `['C', 'R', 'T']`), and recommended GA slot numbers.
3. **Lookup Standard DPT Usage:**
   ```json
   kb_lookup_dpt(dpt="1.001")
   kb_lookup_dpt(dpt="20.102")
   ```
   Returns all standard functional blocks, object names, and required flags for that DPT.
4. **Standard Addressing Structure:**
   Check `kb_get_entry(entry_id="3_level_standard")` for standard Main/Middle/Sub GA division across building trades.

---

## 3. Systematic Workflow: Step-by-Step

### Phase 1: Status & Orientation (Always Start Here)
1. **Check System Status:**
   ```json
   status()
   ```
   Inspect:
   - `project.open`: Is a project currently open?
   - `project.group_address_style`: E.g. `ThreeLevel` (`Main/Middle/Sub`), `TwoLevel`, or `Free`.
   - `connection.connected`: Is an active bus connection available?
   - `keyring.loaded`: Are KNX Data Secure keys available?

2. **Locate Devices:**
   ```json
   project_list_devices()
   ```
   Filter by name or individual address if needed.

3. **Inspect Device Metadata & Intent:**
   ```json
   project_get_device(node_id=12)
   // or by physical address:
   project_get_device(device_address="1.1.20")
   ```
   *Tip:* If device or room names are empty, discover functional context by checking linked group addresses:
   ```json
   project_list_group_addresses(name_contains="Kitchen")
   project_get_ga_assignments(group_address_id=105)
   ```

---

### Phase 2: Actuator & Sensor Channel Assignment (Finding Free Slots)

When assigning a function to a multi-channel actuator (e.g. 16-fold switch actuator) or modular device (e.g. touch panel, room controller):

1. **Find Free Channels/Slots Automatically:**
   ```json
   project_find_free_slots(node_id=12)
   ```
   - Scans channel selectors using heuristic scoring (prioritizes true functional selectors over binary auxiliary parameters).
   - Returns a structured list of occupied channels vs. free/unassigned channels.

2. **Select an Available Slot:**
   Pick the first available slot that matches the required capacity (e.g. Channel D).

---

### Phase 3: Parameter Configuration (The Golden Path)

#### Option A: High-Level Setup (Recommended)
Use `project_setup_parameters` to activate modular functions and configure parameters in one step. It automatically resolves dependency chains and enables parent selectors in the required order:

```json
project_setup_parameters(
  node_id=12,
  parameters=[
    {"path": "Channel D / Operating mode", "value_label": "Staircase light"},
    {"path": "Channel D / Staircase time [s]", "value": "180"}
  ]
)
```

#### Option B: Single Parameter Writes (Fallback Only)
Use `project_set_parameter` only for single, isolated parameter tweaks where no parent gating selectors need to be resolved. For activating channels, changing modes, or multi-parameter setups, always use **Option A (`project_setup_parameters`)**.

```json
project_set_parameter(
  node_id=12,
  path="Channel D / Operating mode",
  value_label="Switch"
)
```
**Verification & Active State:** `project_set_parameter` directly edits currently active parameters and returns `{"active": true, "is_effective": true, ...}`:
- `is_effective: true` (or `active: true`): the parameter value was updated and is currently taking effect in the active UI.
- If a parameter is currently dormant/inactive (hidden behind a disabled parent selector), `project_set_parameter` rejects the edit with an error. Use **Option A (`project_setup_parameters`)** instead to automatically resolve and activate the gating path.

#### Option C: Large Devices (Touch Panels, Room Controllers, OpenKNX)
Devices with hundreds or thousands of parameters should never be queried in full at once:
1. Fetch the skeleton outline:
   ```json
   project_parameter_tree(node_id=60, outline=true)
   ```
2. Read only the specific section using the returned `subtree_id` and `tree_token`:
   ```json
   project_parameter_tree(node_id=60, subtree_id="0.3", tree_token="...")
   ```

---

### Phase 4: Group Addresses & Com-Object Linking

After modifying channel functions or operating modes, com-objects dynamically change. Always re-list com-objects:
```json
project_list_com_objects(node_id=12)
```

#### The Golden Rules of KNX Linking:
1. **Strict 1-Transmitter Rule:**
   - Exactly **ONE** communication object per Group Address may have `is_sending=true` (the command initiator, e.g. pushbutton switch object).
   - Actuator inputs and receivers must have `is_sending=false`.
2. **Status & Feedback Linking:**
   - Every actuator status/feedback object must be linked to its dedicated status GA with `is_sending=true` (on the actuator side).
   - Pushbuttons, LEDs, and touchpanel indicators link to the status GA with `is_sending=false` to remain synchronized.
3. **DPT Compatibility:**
   - All com-objects on the same Group Address must share identical or compatible Datapoint Types.

#### Atomic Execution with `project_batch`:
Create group addresses and link communication objects atomically in a single transaction with automatic rollback on error:

```json
project_batch(
  operations=[
    {
      "op": "create_group_address",
      "params": {"address": "1/1/30", "name": "Kitchen Ceiling Light Switch"}
    },
    {
      "op": "create_group_address",
      "params": {"address": "1/1/31", "name": "Kitchen Ceiling Light Status"}
    },
    {
      "op": "link_com_object",
      "params": {
        "node_id": 12,
        "com_object_ref_id": "M-0008_..._O-10",
        "group_address": "1/1/30",
        "is_sending": false
      }
    },
    {
      "op": "link_com_object",
      "params": {
        "node_id": 12,
        "com_object_ref_id": "M-0008_..._O-11",
        "group_address": "1/1/31",
        "is_sending": true
      }
    },
    {
      "op": "link_com_object",
      "params": {
        "node_id": 5,
        "com_object_ref_id": "M-0002_..._O-1",
        "group_address": "1/1/30",
        "is_sending": true
      }
    },
    {
      "op": "link_com_object",
      "params": {
        "node_id": 5,
        "com_object_ref_id": "M-0002_..._O-2",
        "group_address": "1/1/31",
        "is_sending": false
      }
    }
  ]
)
```

---

### Phase 5: Commissioning & Physical Bus Operations

Bus programming directly writes firmware configuration to device flash memory.

#### Preparation
Ensure an active gateway connection:
```json
connection_connect()
```
Or scan and connect to a discovered gateway:
```json
connection_scan()
connection_connect_gateway(ip="192.168.1.50")
```

#### Workflow A: Virgin / Unprogrammed Device
1. Put the physical device into programming mode (red LED lights up).
2. Assign the individual address:
   ```json
   connection_assign_individual_address(target_address="1.1.20")
   ```
3. Perform initial full programming:
   ```json
   connection_program_device(node_id=12, scope="FULL")
   ```
   *Important:* Do not run `connection_evaluate_device` on a virgin unprogrammed device (it will fail because the device memory structure does not yet exist).

#### Workflow B: Updating an Already-Programmed Device
Choose the minimal download scope to save bus bandwidth and avoid unnecessary restarts:
- **`PARAMETERS`**: Fast parameter update (no link table changes).
- **`GROUP_COMMUNICATION`**: Only group address assignments changed.
- **`PARAMETERS_AND_GROUP_COMMUNICATION`**: Both parameters and link tables changed.
- **`APPLICATION`**: Full application reload (e.g. after major functional/channel restructuring).

#### Workflow C: Changing Individual Address of an Existing Device
1. Update the address in the project:
   ```json
   project_set_individual_address(node_id=12, address="1.1.25")
   ```
2. Program via overwrite scope:
   ```json
   connection_program_device(
     node_id=12,
     scope="OVERWRITE_INDIVIDUAL_ADDRESS",
     current_address="1.1.20"
   )
   ```

---

### Phase 6: Live Diagnosis & Telegram Verification

A successful project edit or bus download confirms only that the configuration was loaded. To verify physical behavior:

1. **Read Live State from Bus:**
   ```json
   monitor_read_value(address="1/1/31")
   ```
   Sends a `GroupValueRead` telegram and waits for the actuator's `GroupValueResponse`.

2. **Query Recent Bus Traffic:**
   ```json
   monitor_telegrams(limit=20)
   ```

3. **Check Auxiliary Objects:**
   If a channel does not switch as expected, verify whether a safety lock (`Lock object`), priority control (`Forced control`), or local manual override is active on the device.

---

### Phase 7: Topology & Space Management (Action-Based)

- **Spaces & Locations:** Use `project_manage_space(action="create"|"rename"|"set_type"|"move"|"remove", ...)`
- **Topology (Areas & Lines):** Use `project_manage_topology(action="create_area"|"rename_area"|"remove_area"|"create_line"|"rename_line"|"remove_line", ...)`

