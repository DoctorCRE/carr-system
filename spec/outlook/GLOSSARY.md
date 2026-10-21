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
