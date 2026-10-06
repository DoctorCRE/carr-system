#!/usr/bin/env python3
"""Read the generated CARR GRANTS section from ``db/schema.sql`` safely.

The snapshot is the canonical reconstruction declaration for privilege bundles.
Consumers must reuse its emitted GRANT statements rather than maintain another
table/function privilege list that can drift from the rebuild path.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable


SECTION_MARKER = "-- CARR GRANTS (bin/schema-snapshot.sh) — not produced by pg_dump."
SECTION_END = "-- PostgreSQL database dump"

# Keep this grammar in lockstep with the six format() calls in
# bin/schema-snapshot.sh.  The old ``.+`` parser admitted an arbitrary second
# statement so long as the line eventually ended in ``to carr_writer;``.
# Catalog names emitted by that generator are deliberately unquoted lower-case
# identifiers; a future generator expansion must update this executable grammar
# before a new SQL shape can be consumed by a privileged provisioner.
IDENT = r"[a-z_][a-z0-9_$]*"
ROLE = rf"(?P<grantee>{IDENT})"
PRIVILEGES = r"[a-z_]+(?:, [a-z_]+)*"
COLUMNS = rf"{IDENT}(?:, {IDENT})*"
FUNCTION_TYPE = rf"(?:{IDENT}\.)?{IDENT}(?: {IDENT})*(?:\[\])?"
# pg_get_function_identity_arguments() prefixes a non-IN argument with its
# upper-case mode (0557's ops.meeting_mode_actor() takes only OUT arguments).
# Catalog identifiers are lower-case, so the mode word cannot be mistaken for a
# name. Postgres ignores OUT arguments when identifying a function, and so does
# _snapshot_function_identity() below.
FUNCTION_ARG = rf"(?:(?:OUT|INOUT|VARIADIC) )?{IDENT} {FUNCTION_TYPE}"
FUNCTION_ARGS = rf"(?:{FUNCTION_ARG}(?:, {FUNCTION_ARG})*)?"
FUNCTION_TYPE_ARGS = rf"(?:{FUNCTION_TYPE}(?:, {FUNCTION_TYPE})*)?"
GRANT_PATTERNS = (
    re.compile(
        rf"^grant (?P<privileges>{PRIVILEGES}) on schema (?P<schema>{IDENT}) to {ROLE};$"
    ),
    re.compile(
        rf"^grant (?P<privileges>{PRIVILEGES}) on (?P<relation_kind>table|sequence) "
        rf"(?P<schema>{IDENT})\.(?P<object>{IDENT}) to {ROLE};$"
    ),
    re.compile(
        rf"^grant (?P<privileges>[a-z_]+) \((?P<columns>{COLUMNS})\) on table "
        rf"(?P<schema>{IDENT})\.(?P<object>{IDENT}) to {ROLE};$"
    ),
    re.compile(
        rf"^grant execute on function (?P<schema>{IDENT})\.(?P<object>{IDENT})"
        rf"\((?P<function_args>{FUNCTION_ARGS})\) to {ROLE};$"
    ),
    re.compile(rf"^grant (?P<granted_role>{IDENT}) to {ROLE};$"),
    # The sixth shape, and the only one that takes a privilege away rather than
    # conferring one: the snapshot must re-assert the PUBLIC revokes that its
    # own migrations already applied to production, or a database rebuilt from
    # it hands every function back at Postgres' permissive default. It names no
    # grantee, so it contributes no ACL fact — acl_facts() skips it — and it can
    # never widen anyone's authority, which is why admitting it into a grammar
    # a privileged provisioner consumes is safe.
    re.compile(
        rf"^revoke all on function (?P<schema>{IDENT})\.(?P<object>{IDENT})"
        rf"\((?P<function_args>{FUNCTION_ARGS})\) from public;$"
    ),
)
REVOKE_FROM_PUBLIC = re.compile(r"^revoke all on function .* from public;$")
DERIVED_FUNCTION_GRANT = re.compile(
    rf"^grant execute on function (?P<schema>{IDENT})\.(?P<object>{IDENT})"
    rf"\((?P<function_type_args>{FUNCTION_TYPE_ARGS})\) to {ROLE};$"
)


class SnapshotGrantError(ValueError):
    """The generated grant section is missing or no longer safely parseable."""


AclFact = tuple[str, str, str, bool]


def _split_commas(value: str) -> list[str]:
    """Split a generated SQL list without splitting function arguments."""
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise SnapshotGrantError("unbalanced ACL object list")
        elif char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    if depth != 0:
        raise SnapshotGrantError("unbalanced ACL object list")
    parts.append(value[start:].strip())
    if any(not part for part in parts):
        raise SnapshotGrantError("empty item in ACL object list")
    return parts


# EVERY MULTI-WORD SPELLING format_type() CAN PRINT FOR AN ARGUMENT TYPE.
# pg_get_function_identity_arguments() and oidvectortypes() both render through
# format_type(), which spells exactly these pg_catalog types with a space in
# them (typmods never appear in an argument identity). The list is closed: a
# user-defined type is schema-qualified by the generator's search_path, so an
# unqualified multi-word argument is always one of these.
#
# WHY THE PARSER NEEDS IT (2026-09-26, PR #1297). A function argument may or
# may not carry a name, and a nameless multi-word type is textually identical to
# "name type": `timestamp with time zone` read as name `timestamp`, type `with
# time zone`. render_acl_facts() writes type-only identities, so the moment a
# snapshot refresh absorbed ops.f01_instant_text's grant, acl_facts() read its
# own output back as `ops.f01_instant_text(with time zone)` and the login gate
# failed a correct database. None of the trailing fragments (`with time zone`,
# `varying`, `precision`, ...) is a type, so an argument that is exactly one of
# these spellings can only be a nameless type -- and Postgres' own grammar reads
# it the same way.
MULTIWORD_ARGUMENT_TYPES = frozenset({
    "bit varying",
    "character varying",
    "double precision",
    "time with time zone",
    "time without time zone",
    "timestamp with time zone",
    "timestamp without time zone",
})


def _argument_type(argument: str) -> str:
    """Return the type of one mode-stripped argument, with or without a name."""
    normalized = " ".join(argument.split())
    bare = normalized[:-2] if normalized.endswith("[]") else normalized
    if bare in MULTIWORD_ARGUMENT_TYPES:
        return normalized
    try:
        _argument_name, argument_type = normalized.split(" ", 1)
    except ValueError as exc:
        # The generated grammar admits only name/type pairs; a single-token
        # nameless type reaches acl_facts() through DERIVED_FUNCTION_GRANT and
        # never through here.
        raise SnapshotGrantError(
            f"generated function argument has no name/type pair: {argument}"
        ) from exc
    return argument_type


def _snapshot_function_identity(schema: str, name: str, arguments: str) -> str:
    types: list[str] = []
    if arguments:
        for argument in _split_commas(arguments):
            mode, _, rest = argument.partition(" ")
            if mode == "OUT":
                continue
            if mode in ("INOUT", "VARIADIC"):
                argument = rest
            normalized_type = _argument_type(argument)
            # The snapshot SQL must qualify public composite types so it loads
            # under an empty/default search_path. PostgreSQL's ACL catalog
            # renderer (oidvectortypes) reports public/pg_catalog types without
            # those prefixes on the provisioner's normal connection. Normalize
            # only those two implicit schemas for a stable authority identity.
            for implicit_schema in ("public.", "pg_catalog."):
                if normalized_type.startswith(implicit_schema):
                    normalized_type = normalized_type[len(implicit_schema):]
                    break
            types.append(normalized_type)
    return f"{schema}.{name}({', '.join(types)})"


def match_generated_grant(statement: str) -> re.Match[str]:
    """Match exactly one generator-emitted statement and return its grantee."""
    if (
        not statement.endswith(";")
        or statement.count(";") != 1
        or "--" in statement
        or "/*" in statement
        or "*/" in statement
        or "\n" in statement
        or "\r" in statement
    ):
        raise SnapshotGrantError(
            "CARR GRANTS statement is not one comment-free SQL statement: "
            + statement[:120]
        )
    for pattern in GRANT_PATTERNS:
        match = pattern.fullmatch(statement)
        if match is not None:
            return match
    raise SnapshotGrantError(
        "CARR GRANTS contains SQL outside the schema generator grammar: "
        + statement[:120]
    )


def match_safe_grant(statement: str) -> re.Match[str]:
    """Match a snapshot grant or a derived type-only function grant."""
    try:
        return match_generated_grant(statement)
    except SnapshotGrantError:
        match = DERIVED_FUNCTION_GRANT.fullmatch(statement)
        if match is not None:
            return match
        raise


def carr_grants_section_lines(schema_text: str) -> list[str]:
    """Return non-comment SQL lines from the one generated CARR GRANTS section.

    The generator emits every GRANT on one line. Refusing any other SQL shape is
    intentional: silently skipping a wrapped or newly generated statement would
    provision a role that only partly matches the repository declaration.
    """
    lines = schema_text.splitlines()
    markers = [i for i, line in enumerate(lines) if line == SECTION_MARKER]
    if len(markers) != 1:
        raise SnapshotGrantError(
            f"expected exactly one CARR GRANTS marker, found {len(markers)}"
        )
    start = markers[0] + 1
    try:
        end = next(i for i in range(start, len(lines)) if lines[i] == SECTION_END)
    except StopIteration as exc:
        raise SnapshotGrantError("CARR GRANTS section has no dump boundary") from exc

    sql_lines: list[str] = []
    for raw in lines[start:end]:
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        match_generated_grant(line)
        sql_lines.append(line)
    if not sql_lines:
        raise SnapshotGrantError("CARR GRANTS section is empty")
    return sql_lines


def grants_to_role(schema_text: str, role: str) -> list[str]:
    """Return exact canonical statements whose grantee set is only ``role``."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", role):
        raise SnapshotGrantError(f"unsafe role name {role!r}")
    selected: list[str] = []
    for statement in carr_grants_section_lines(schema_text):
        # A PUBLIC revoke names no grantee and confers nothing, so it belongs to
        # no role's grant set. It is still validated above, against the grammar,
        # before being skipped.
        if REVOKE_FROM_PUBLIC.fullmatch(statement):
            match_generated_grant(statement)
            continue
        match = match_generated_grant(statement)
        grantee = match.group("grantee")
        if role.lower() == grantee:
            selected.append(statement)
    if not selected:
        raise SnapshotGrantError(f"CARR GRANTS contains no statements for {role}")
    return selected
def load_grants_to_role(schema_path: Path, role: str) -> list[str]:
    return grants_to_role(schema_path.read_text(encoding="utf-8"), role)


def acl_facts(statements: Iterable[str]) -> tuple[AclFact, ...]:
    """Expand canonical object GRANTs into atomic, non-grantable ACL facts.

    The snapshot generator emits no ``WITH GRANT OPTION`` form.  Grantability
    therefore belongs in the comparison key and is always false canonically;
    a catalog row with it set is excess authority, not an equivalent GRANT.
    """
    facts: set[AclFact] = set()
    for statement in statements:
        # Revokes remove a default rather than conferring authority, so they
        # produce no ACL fact — the fact set stays a set of things somebody CAN
        # do, which is what every comparison built on it assumes.
        if REVOKE_FROM_PUBLIC.fullmatch(statement):
            continue
        match = match_safe_grant(statement)
        groups = match.groupdict()
        if groups.get("granted_role") is not None:
            continue
        privileges = (groups.get("privileges") or "execute").split(", ")
        schema = groups["schema"]
        obj = groups.get("object")
        columns = groups.get("columns")
        function_args = groups.get("function_args")
        function_type_args = groups.get("function_type_args")
        if function_args is not None or function_type_args is not None:
            if obj is None:
                raise SnapshotGrantError("function GRANT has no function name")
            identity = (
                _snapshot_function_identity(schema, obj, function_args)
                if function_args is not None
                else f"{schema}.{obj}({', '.join(_split_commas(function_type_args)) if function_type_args else ''})"
            )
            kind = "function"
            for privilege in privileges:
                facts.add((kind, identity, privilege, False))
        elif columns is not None:
            for column in columns.split(", "):
                identity = f"{schema}.{obj}({column})"
                for privilege in privileges:
                    facts.add(("column", identity, privilege, False))
        elif obj is not None:
            identity = f"{schema}.{obj}"
            kind = groups["relation_kind"]
            for privilege in privileges:
                facts.add((kind, identity, privilege, False))
        else:
            for privilege in privileges:
                facts.add(("schema", schema, privilege, False))
    return tuple(sorted(facts))


LEDGER_COPY = "COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;"
SAFE_NAME = r"[a-z_][a-z0-9_$]*"
SAFE_QUALIFIED = rf"(?:{SAFE_NAME}\.)?{SAFE_NAME}"
ACL_STATEMENT = re.compile(r"\b(?:grant|revoke)\b[^;]*;", re.IGNORECASE)
ACL_SHAPE = re.compile(
    r"^(?P<action>grant|revoke)\s+(?P<privileges>.+?)\s+on\s+"
    r"(?:(?P<kind>schema|table|sequence|function)\s+)?(?P<objects>.+?)\s+"
    r"(?P<direction>to|from)\s+(?P<grantees>[a-z_][a-z0-9_$]*(?:\s*,\s*[a-z_][a-z0-9_$]*)*)"
    r"(?P<option>\s+with\s+grant\s+option|\s+(?:cascade|restrict))?;$",
    re.IGNORECASE,
)
FUNCTION_OBJECT = re.compile(
    rf"^(?P<name>{SAFE_QUALIFIED})\((?P<arguments>[a-z0-9_$.,\[\] ]*)\)$"
)
RELATION_OBJECT = re.compile(rf"^{SAFE_QUALIFIED}$")
SCHEMA_OBJECT = re.compile(rf"^{SAFE_NAME}$")
PRIVILEGE = re.compile(
    rf"^(?P<name>[a-z_]+)(?:\s*\((?P<columns>{SAFE_NAME}(?:\s*,\s*{SAFE_NAME})*)\))?$"
)


def snapshot_applied_migrations(schema_text: str) -> dict[str, str]:
    """Read the exact filename/digest ledger embedded in the schema snapshot."""
    lines = schema_text.splitlines()
    markers = [index for index, line in enumerate(lines) if line == LEDGER_COPY]
    if len(markers) != 1:
        raise SnapshotGrantError(
            f"expected exactly one schema_migrations COPY, found {len(markers)}"
        )
    applied: dict[str, str] = {}
    for line in lines[markers[0] + 1:]:
        if line == r"\.":
            break
        fields = line.split("\t")
        if len(fields) != 3 or not re.fullmatch(r"[0-9a-f]{64}", fields[1]):
            raise SnapshotGrantError("malformed schema_migrations snapshot row")
        if fields[0] in applied:
            raise SnapshotGrantError(f"duplicate schema migration ledger row: {fields[0]}")
        applied[fields[0]] = fields[1]
    else:
        raise SnapshotGrantError("schema_migrations COPY has no terminator")
    return applied


def _scrub_sql(sql: str) -> str:
    """Remove comments and quoted string contents before locating ACL statements."""
    out: list[str] = []
    index = 0
    block_depth = 0
    while index < len(sql):
        if block_depth:
            if sql.startswith("/*", index):
                block_depth += 1
                index += 2
            elif sql.startswith("*/", index):
                block_depth -= 1
                index += 2
            else:
                index += 1
            out.append(" ")
            continue
        if sql.startswith("--", index):
            end = sql.find("\n", index)
            if end < 0:
                out.append(" " * (len(sql) - index))
                break
            out.append(" " * (end - index))
            index = end
            continue
        if sql.startswith("/*", index):
            block_depth = 1
            out.append("  ")
            index += 2
            continue
        if sql[index] == "'":
            start = index
            index += 1
            while index < len(sql):
                if sql[index] == "'":
                    if index + 1 < len(sql) and sql[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                raise SnapshotGrantError("unterminated SQL string in migration")
            out.append(" " * (index - start))
            continue
        out.append(sql[index])
        index += 1
    if block_depth:
        raise SnapshotGrantError("unterminated SQL block comment in migration")
    return "".join(out)


def _qualify(name: str, default_schema: str = "public") -> str:
    normalized = "".join(name.lower().split())
    if not RELATION_OBJECT.fullmatch(normalized):
        raise SnapshotGrantError(f"unsafe ACL object identity: {name}")
    return normalized if "." in normalized else f"{default_schema}.{normalized}"


def _migration_acl_operation(statement: str, role: str):
    normalized = " ".join(statement.lower().split())
    match = ACL_SHAPE.fullmatch(normalized)
    if match is None:
        if re.search(rf"\b{re.escape(role)}\b", normalized):
            raise SnapshotGrantError(
                "pending migration ACL is outside the deterministic grammar: "
                + normalized[:160]
            )
        return None
    groups = match.groupdict()
    grantees = [item.strip() for item in groups["grantees"].split(",")]
    if role not in grantees:
        return None
    if groups["action"] == "grant" and groups["direction"] != "to":
        raise SnapshotGrantError("GRANT uses a non-TO authority target")
    if groups["action"] == "revoke" and groups["direction"] != "from":
        raise SnapshotGrantError("REVOKE uses a non-FROM authority target")
    if groups.get("option") and "grant option" in groups["option"]:
        raise SnapshotGrantError("canonical carr_writer authority cannot be grantable")

    raw_kind = groups.get("kind") or "table"
    objects: list[tuple[str, str]] = []
    for raw_object in _split_commas(groups["objects"]):
        if raw_kind == "function":
            function = FUNCTION_OBJECT.fullmatch(" ".join(raw_object.split()))
            if function is None:
                raise SnapshotGrantError(f"unsafe function ACL identity: {raw_object}")
            qualified = _qualify(function.group("name"))
            arguments = [" ".join(arg.split()) for arg in _split_commas(
                function.group("arguments")
            )] if function.group("arguments") else []
            objects.append(("function", f"{qualified}({', '.join(arguments)})"))
        elif raw_kind == "schema":
            schema = "".join(raw_object.split())
            if not SCHEMA_OBJECT.fullmatch(schema):
                raise SnapshotGrantError(f"unsafe schema ACL identity: {raw_object}")
            objects.append(("schema", schema))
        else:
            objects.append((raw_kind, _qualify(raw_object)))

    raw_privileges = _split_commas(groups["privileges"])
    privileges: list[tuple[str, str | None]] = []
    for raw_privilege in raw_privileges:
        privilege = PRIVILEGE.fullmatch(" ".join(raw_privilege.split()))
        if privilege is None:
            raise SnapshotGrantError(f"unsafe ACL privilege shape: {raw_privilege}")
        privilege_name = privilege.group("name")
        if privilege_name == "all":
            if len(raw_privileges) != 1 or privilege.group("columns") is not None:
                raise SnapshotGrantError("ALL cannot be combined with ACL privileges")
            privileges.append(("all", None))
            continue
        columns = privilege.group("columns")
        if columns and raw_kind != "table":
            raise SnapshotGrantError("column ACL requires a table")
        if columns:
            for column in columns.split(","):
                privileges.append((privilege_name, column.strip()))
        else:
            privileges.append((privilege_name, None))
    return groups["action"], objects, privileges


# A dollar-quoted body: `$$ ... $$` or `$tag$ ... $tag$`. The tag alternative
# includes the empty string so the bare form matches its own closing delimiter.
DOLLAR_QUOTED = re.compile(
    r"\$(?P<tag>[A-Za-z_][A-Za-z0-9_]*|)\$(?P<body>.*?)\$(?P=tag)\$", re.DOTALL
)

# EVERY SPELLING THIS COMPOSER KNOWS OF THAT COULD MOVE AUTHORITY. A dollar-
# quoted body carrying one of these outside a statement the composer parsed is
# authority it cannot see, and stays a review stop. A body carrying none of them
# names the role without conferring anything -- `create role`, a `rolname = '...'`
# probe, a function returning the bundle's name as a literal -- and those are
# forgiven, because counting them as unparsed authority made the first bundle
# introduced by a still-pending migration impossible to compose (2026-09-14).
AUTHORITY_VERB = re.compile(
    r"\b(?:grant|revoke|reassign\s+owned|set\s+role|security\s+definer)\b"
    r"|\balter\s+(?:default\s+privileges|role|table[\s\S]*?\bowner\s+to)\b"
    r"|\bowner\s+to\b",
    re.IGNORECASE,
)


def _migration_acl_statements(sql: str, role: str) -> list[str]:
    scrubbed = _scrub_sql(sql)
    statements = [match.group(0) for match in ACL_STATEMENT.finditer(scrubbed)]
    # Every uncommented occurrence naming the target must belong to a complete
    # semicolon-terminated ACL statement. This turns new syntax into a review
    # stop instead of silently omitting authority from the plan.
    #
    # THE ONE EXEMPTION, AND ITS TEST. An occurrence inside a dollar-quoted body
    # is forgiven when that body, with its parsed ACL statements removed, names
    # no authority verb at all. That is the shape of a migration CREATING a
    # capability bundle and of a function returning the bundle's name: neither
    # confers anything. A body that still carries an authority verb after its
    # parsed statements are removed is the hidden-authority case this check
    # exists for, and it stops review exactly as before.
    forgiven = 0
    for match in DOLLAR_QUOTED.finditer(scrubbed):
        body = match.group("body")
        occurrences = len(re.findall(rf"\b{re.escape(role)}\b", body))
        if not occurrences:
            continue
        parsed = [one.group(0) for one in ACL_STATEMENT.finditer(body)]
        remainder = body
        for statement in parsed:
            remainder = remainder.replace(statement, " ", 1)
        if AUTHORITY_VERB.search(remainder):
            continue
        forgiven += occurrences - sum(
            len(re.findall(rf"\b{re.escape(role)}\b", statement)) for statement in parsed
        )
    named_occurrences = len(re.findall(rf"\b{re.escape(role)}\b", scrubbed))
    statement_occurrences = sum(
        len(re.findall(rf"\b{re.escape(role)}\b", statement))
        for statement in statements
    )
    if named_occurrences - forgiven != statement_occurrences:
        raise SnapshotGrantError(
            f"pending migration mentions {role} outside a parsed ACL statement"
        )
    return statements


def compose_grants_to_role(
    schema_text: str, migrations: Iterable[tuple[str, str]], role: str
) -> list[str]:
    """Compose snapshot ACLs plus post-snapshot migration ACL operations.

    This is the current rebuilt-schema authority source. It follows the same
    filename ordering and embedded ledger boundary as ``tools/migrate.py`` and
    derives facts from committed SQL; it does not maintain a second ACL list.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]*", role):
        raise SnapshotGrantError(f"unsafe role name {role!r}")
    applied = snapshot_applied_migrations(schema_text)
    ordered = sorted(migrations, key=lambda item: item[0])
    if len({name for name, _sql in ordered}) != len(ordered):
        raise SnapshotGrantError("duplicate migration filename in authority plan")
    for name, sql in ordered:
        if name in applied:
            digest = hashlib.sha256(sql.encode()).hexdigest()
            if digest != applied[name]:
                raise SnapshotGrantError(
                    f"{name} digest differs from the schema snapshot ledger"
                )

    # A BUNDLE WHOSE MIGRATION IS STILL PENDING HAS NO SNAPSHOT ACLs, BY DESIGN.
    # bin/schema-snapshot.sh's role preamble deliberately creates capability
    # bundles ahead of the migration that grants to them, so a pending migration
    # can grant on a fresh cluster; tools/test-schema-snapshot-grants.py's
    # ROLE_GRANT_MIGRATIONS map records exactly which bundles are in that state.
    # For those, the snapshot is CORRECT to be silent -- it is production truth,
    # and in production the role holds nothing yet -- so composition starts from
    # an empty fact set and the pending series supplies the whole plan. It still
    # refuses to return nothing: a role no committed SQL grants to anywhere is a
    # typo, not a pending bundle. (2026-09-14, carr_gate_zero_producer, the
    # first role introduced by a migration pending at composition time.)
    try:
        facts = set(acl_facts(grants_to_role(schema_text, role)))
    except SnapshotGrantError as exc:
        if f"CARR GRANTS contains no statements for {role}" != str(exc):
            raise
        facts = set()
    for name, sql in ordered:
        if name in applied:
            continue
        for statement in _migration_acl_statements(sql, role):
            operation = _migration_acl_operation(statement, role)
            if operation is None:
                continue
            action, objects, privileges = operation
            for kind, identity in objects:
                if action == "revoke" and privileges == [("all", None)]:
                    facts = {
                        fact for fact in facts
                        if not (fact[0] == kind and fact[1] == identity)
                    }
                    continue
                for privilege, column in privileges:
                    fact_kind = "column" if column is not None else kind
                    fact_identity = (
                        f"{identity}({column})" if column is not None else identity
                    )
                    fact: AclFact = (fact_kind, fact_identity, privilege, False)
                    if action == "grant":
                        facts.add(fact)
                    else:
                        facts.discard(fact)
    if not facts:
        raise SnapshotGrantError(
            f"neither the schema snapshot nor any pending migration grants to {role}"
        )
    return render_acl_facts(facts, role)


def render_acl_facts(facts: Iterable[AclFact], role: str) -> list[str]:
    """Render derived, atomic non-grantable facts through the safe grammar."""
    statements: list[str] = []
    for kind, identity, privilege, grantable in sorted(set(facts)):
        if grantable:
            raise SnapshotGrantError("canonical ACL fact cannot be grantable")
        if kind == "column":
            relation, column = identity[:-1].rsplit("(", 1)
            statement = f"grant {privilege} ({column}) on table {relation} to {role};"
        elif kind == "function":
            statement = f"grant {privilege} on function {identity} to {role};"
        elif kind in {"schema", "table", "sequence"}:
            statement = f"grant {privilege} on {kind} {identity} to {role};"
        else:
            raise SnapshotGrantError(f"cannot render unsupported ACL kind: {kind}")
        # The renderer must remain accepted by the existing privileged parser.
        match_safe_grant(statement)
        statements.append(statement)
    return statements


def load_current_grants_to_role(
    schema_path: Path, migrations_dir: Path, role: str
) -> list[str]:
    migrations = [
        (path.name, path.read_text(encoding="utf-8"))
        for path in sorted(migrations_dir.iterdir())
        if path.suffix == ".sql"
    ]
    return compose_grants_to_role(
        schema_path.read_text(encoding="utf-8"), migrations, role
    )


def render_statements(statements: Iterable[str]) -> str:
    """Render an executable script without changing canonical statement bytes."""
    return "\n".join(statements) + "\n"
