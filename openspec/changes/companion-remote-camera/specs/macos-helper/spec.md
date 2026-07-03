## ADDED Requirements

### Requirement: The helper SHALL expose a `camsnap` subcommand that captures one JPEG frame
The helper bundle SHALL expose a `camsnap` subcommand that opens the default
video capture device, grabs a single still frame, encodes it as JPEG, and prints
a schema-versioned JSON object to stdout carrying the format, pixel width and
height, and the base64 image bytes. It SHALL accept optional parameters for
target dimensions and JPEG quality. On camera TCC denial it SHALL fail fast with
the helper's denial exit code (3), never emitting a partial or fake frame. The
subcommand SHALL live on the existing `diting-tianer` bundle so it inherits that
bundle's cdhash and single TCC subject, rather than shipping a second bundle.

#### Scenario: One-shot frame to stdout
- **WHEN** `camsnap` is invoked and camera access is authorized
- **THEN** it prints one JSON object with `schema`, `fmt`, width, height, and base64 JPEG bytes, then exits 0

#### Scenario: Camera denied fails loud
- **WHEN** `camsnap` is invoked and camera access is denied or restricted
- **THEN** it exits with code 3 and emits no image bytes

#### Scenario: camsnap response carries its own schema integer
- **WHEN** the `camsnap` JSON output is parsed
- **THEN** it carries a `schema` integer independent of the `wifi-scan` and `associate` schemas, versioned additively

## MODIFIED Requirements

### Requirement: The helper SHALL request Location, Bluetooth, and Notifications permissions in a sequenced flow at install time
When launched as a GUI app (`open <bundle>`), the helper SHALL request the TCC permissions in the order Location → Bluetooth → Notifications → Camera. Each request SHALL fire only after the previous one's authorization callback resolves to a non-`.notDetermined` state (Allow, Don't Allow, restricted, or denied — any settled state). The user SHALL see at most one macOS TCC prompt on screen at any time during install, on top of the persistent helper status window. The Camera row SHALL be present because adding `NSCameraUsageDescription` to the bundle changes its cdhash and forces all TCC grants to be re-requested once on the next launch; the sequenced flow SHALL re-request every permission in that case rather than leaving stale rows.

The status window SHALL render one line per requested permission and update each line's status text as the corresponding callback resolves. The window SHALL auto-close ~4 seconds after the final permission's state has settled.

The status window SHALL be laid out top-aligned: its content SHALL be pinned to the top of the content view with consistent padding and the window SHALL be sized to fit its content, leaving no large empty region. The window SHALL show, from the top down, the bundle's app icon (the diting logo), a bold title, a secondary-color explanatory paragraph, and one status row per permission. Each status row SHALL carry a leading status glyph whose symbol and color reflect that permission's state — pending (not yet reached), in-progress (awaiting the user's decision, rendered in the diting brand color), granted, or denied/restricted — alongside the permission's status text.

If any permission resolves to denied or restricted, the helper SHALL continue to the next permission rather than aborting the flow, and the status line for the denied permission SHALL include a "open System Settings → Privacy & Security → ..." hint.

#### Scenario: User clicks Allow on all four
- **WHEN** the user runs install.sh and clicks Allow on Location, then Bluetooth, then Notifications, then Camera
- **THEN** macOS shows exactly one prompt at a time, never two simultaneously
- **AND** the status window shows each permission's row turn from in-progress to a granted glyph as it lands, in order Location → Bluetooth → Notifications → Camera
- **AND** the window auto-closes ~4 seconds after the fourth grant

#### Scenario: User denies a permission mid-flow
- **WHEN** the user clicks Don't Allow on Bluetooth
- **THEN** the status window shows the Bluetooth row with a denied glyph and a Settings hint
- **AND** the helper still requests Notifications then Camera next (does not abort the flow)
- **AND** the window auto-closes after the final outcome resolves

#### Scenario: cdhash change forces a one-time re-grant
- **WHEN** a user who previously granted Location/Bluetooth/Notifications launches the new bundle carrying `NSCameraUsageDescription`
- **THEN** the sequenced flow re-requests all four permissions once because the changed cdhash invalidated the prior grants

#### Scenario: Window is legibly laid out
- **WHEN** the helper status window appears
- **THEN** its content is top-aligned with the diting app icon at the top and one status row per permission, each with a leading status glyph
- **AND** there is no large empty region above or below the content
