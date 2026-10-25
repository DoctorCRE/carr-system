# Outlook format atlas language

These terms distinguish observed storage structure, decoded meaning, and application declarations.

## Language

**Store atlas**:
A catalog of known and unknown storage structures with evidence for each interpretation.

**Surface atlas**:
A catalog of an application's static declarations and recovered URL grammar.
_Avoid_: Runtime capability list.

**Wire field ID**:
An identifier encoded in a stored record that distinguishes one serialized field from another.
_Avoid_: Output column, string position, object offset.

**String-run selector**:
A rule that identifies text by its order or shape without establishing a wire field ID.

**Literal grammar**:
The host, path, and query-key vocabulary present in a complete URL literal.
_Avoid_: Validated invocation contract.

**Reconstructed grammar**:
URL vocabulary inferred from separate constants and their use in a route builder or parser.

**First seen**:
The first Outlook build in which this atlas history observes an entry.
_Avoid_: Introduction version.

**Decoded-byte coverage**:
The share of record bytes whose meaning has a supported interpretation, with overlapping spans counted once.
_Avoid_: Message recovery rate.

**Object class**:
A serialized object's encoded type discriminator.
_Avoid_: Property ID, envelope tag.

**Envelope tag**:
An encoded discriminator in the wrapper around a serialized object.
_Avoid_: Object class, property ID.

**Object offset**:
A byte displacement measured from the start of a serialized object's body.
_Avoid_: Wire field ID, envelope-relative offset.

**Decoder key**:
A stable identity used to select a decoded meaning independently of its display name.

**Display name**:
The human-readable label attached to a decoded meaning.
_Avoid_: Wire field ID.


**Verified payload bytes**:
Decompressed bytes from a block whose bounds, header checksum, payload checksum, and exact decompression all pass.
A block with verified bytes can still have undecodable record structure.

**Unverified block**:
A detected block signature whose block bounds, checksums, or decompression cannot be verified.
Its claimed decompressed size does not count toward decoded-byte coverage.

**Undecodable block**:
A verified payload whose candidate record bounds prevent a complete structural partition.
Its bytes count toward coverage as wholly unframed, without tentative decoded credit.

**Audit-guided reconstruction**:
Extraction of route and key vocabulary from a pinned static analysis of one exact binary build.
A changed binary does not qualify until its constants and control flow receive a new audit.

**Outlook machine settings**:
The local operator's mailbox identity, record owner, service enablement, and paths.
Credential entries are pointers to private files, never credential values.

**Granted copy scope**:
The profile, private snapshot root, and intake command bound into the executable granted Full Disk Access.
Changing settings does not widen an existing executable's scope.
