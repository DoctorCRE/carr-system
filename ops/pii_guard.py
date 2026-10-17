"""Hash-only public-source identity check; plaintext never enters findings.

The public salt prevents accidental reuse of unsalted identity fingerprints.
It is not encryption and does not prevent guessing a known name. Refresh the
corpus with read-only record-layer calls on the private operator machine.
"""
import hashlib
import functools
import itertools
import json
import pathlib
import re
import unicodedata


def tokens(text):
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", folded)


def fingerprint(value, salt):
    return hashlib.sha256((salt + "\0" + "".join(tokens(value))).encode()).hexdigest()


def load_corpus(path):
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if (not isinstance(data, dict)
            or data.get("schema") != "public-source-identities/v1"
            or not isinstance(data.get("salt"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", data["salt"])
            or type(data.get("max_tokens")) is not int
            or not 1 <= data["max_tokens"] <= 64
            or not isinstance(data.get("hashes"), list)
            or not data["hashes"]
            or any(not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h)
                   for h in data["hashes"])):
        raise ValueError("invalid identity corpus")
    return data


def check(source, corpus_path, *, output):
    """Return 1 on exposure, 2 on invalid corpus; print only file:line.

    Tokens retain source lines so names split across lines are also checked.
    N-grams normalize punctuation, case, accents and whitespace identically
    to the locally generated corpus. No match text or hash is emitted.
    """
    try:
        corpus = load_corpus(corpus_path)
    except (OSError, ValueError):
        output.write("ops/config/public-source-identities.v1.json:1\n")
        return 2
    return _check_corpus(source, corpus, output=output)


def _text_views(text):
    """Raw text and JSON string values, each mapped to original source offsets."""
    # JSON string literals occur in JSON files, code, and COPY reference prose.
    for match in re.finditer(r'"(?:[^"\\]|\\.)*"', text):
        raw = match[0]
        if "\\" not in raw:
            # Its normalized words and source offsets already occur in the raw
            # view. Only escapes can add a represented identity to that view.
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        positions = []
        i = 1
        while i < len(raw) - 1:
            begin = i
            if raw[i] == "\\":
                i += 6 if raw[i + 1] == "u" else 2
                # JSON surrogate pairs represent one Unicode character.
                if (raw[begin:begin + 2] == "\\u" and i + 6 <= len(raw) - 1
                        and raw[i:i + 2] == "\\u"
                        and 0xD800 <= int(raw[begin + 2:begin + 6], 16) <= 0xDBFF
                        and 0xDC00 <= int(raw[i + 2:i + 6], 16) <= 0xDFFF):
                    i += 6
            else:
                i += 1
            positions.append((match.start() + begin, match.start() + i))
        if len(value) == len(positions):
            yield value, positions


def identity_spans(text, corpus):
    """One matcher for detection and projection, including serialized strings."""
    banned = set(corpus["hashes"])
    found = set()

    def matching_digest(phrase):
        digest = hashlib.sha256((corpus["salt"] + "\0" + phrase).encode()).hexdigest()
        return digest if digest in banned else None

    # Dumps repeat SQL vocabulary and reference prose millions of times. Reuse
    # the judgment, never its offsets; every occurrence still yields its span.
    # Both entry count and key length are bounded. Larger phrases are checked
    # uncached, so arbitrary source text cannot inflate retained cache memory.
    cached_digest = functools.lru_cache(maxsize=8192)(matching_digest)
    raw_words = [(word, m.start(), m.end()) for m in re.finditer(r"(?:[^\W_]|[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f])+", text)
                 for word in tokens(m[0])]

    def represented_words():
        for value, positions in _text_views(text):
            yield [(word, positions[m.start()][0], positions[m.end() - 1][1])
                   for m in re.finditer(r"(?:[^\W_]|[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f])+", value) for word in tokens(m[0])]

    for words in itertools.chain([raw_words], represented_words()):
        for start, (_, begin, _) in enumerate(words):
            phrase = ""
            for word, _, end in words[start:start + corpus["max_tokens"]]:
                phrase += word
                digest = cached_digest(phrase) if len(phrase) <= 1024 else matching_digest(phrase)
                if digest is not None:
                    found.add((begin, end, digest))
    return sorted(found, key=lambda row: (row[0], -row[1]))


def _check_corpus(source, corpus, *, output):
    found = set()
    for path, text in source:
        for begin, _, _ in identity_spans(text, corpus):
            found.add((path, text.count("\n", 0, begin) + 1))
    for path, line in sorted(found):
        output.write(f"{path}:{line}\n")
    return int(bool(found))


def synthetic_prose(text, corpus):
    """Replace identities through the detector's source-mapped spans."""
    replacements = []
    consumed = 0
    for begin, end, digest in identity_spans(text, corpus):
        if begin < consumed:
            continue
        original = text[begin:end]
        replacement = (original[0] + "-900" + str(int(digest[:8], 16))
                       if re.fullmatch(r"[CLVP]-\d+", original) else
                       "Example Organization " + digest[:8])
        # Keep line-comment delimiters/newlines when a name spans comments.
        if "\n" in original:
            replacement += "".join("\n" + re.match(r"[ \t]*(?:--)?[ \t]*", line)[0]
                                   for line in original.split("\n")[1:])
        replacements.append((begin, end, replacement))
        consumed = end
    for begin, end, replacement in reversed(replacements):
        text = text[:begin] + replacement + text[end:]
    return text


def _sql_regions(text):
    """Yield (begin, end, projectable) using PostgreSQL dump lexical state.

    Executable strings, identifiers and dollar bodies are opaque. COPY rows
    are opaque except the explicitly exported retrieval reference vocabulary.
    Only comments and COMMENT ON literal metadata can be projected.
    """
    i = 0
    statement = []
    copy_rx = re.compile(r"COPY\s+([^\s(]+)[^;]*FROM stdin;[^\n]*\n", re.I)
    dollar_rx = re.compile(r"\$(?:[a-zA-Z_][a-zA-Z_0-9]*)?\$")
    while i < len(text):
        begin = i
        copy = copy_rx.match(text, i) if text[i] in "Cc" else None
        if copy and not "".join(statement).strip():
            header_end = copy.end()
            end = re.search(r"(?m)^\\\.\r?$", text[header_end:])
            if end is None:
                raise ValueError("unterminated COPY")
            rows_end = header_end + end.start()
            yield i, header_end, False
            yield header_end, rows_end, copy[1] == "public.retrieval_proposal"
            i = header_end + end.end()
            yield rows_end, i, False
            statement = []
            continue
        if text.startswith("--", i):
            # Group adjacent comments so the matcher sees line-spanning names.
            end = text.find("\n", i)
            i = len(text) if end < 0 else end + 1
            while i < len(text) and re.match(r"[ \t]*--", text[i:]):
                end = text.find("\n", i)
                i = len(text) if end < 0 else end + 1
            yield begin, i, True
            continue
        if text.startswith("/*", i):
            depth = 1
            i += 2
            while i < len(text) and depth:
                if text.startswith("/*", i):
                    depth += 1
                    i += 2
                elif text.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth:
                raise ValueError("unterminated comment")
            yield begin, i, True
            continue
        dollar = dollar_rx.match(text, i) if text[i] == "$" else None
        if dollar:
            end = text.find(dollar[0], dollar.end())
            if end < 0:
                raise ValueError("unterminated dollar body")
            i = end + len(dollar[0])
            declaration = "".join(statement)
            procedural = (re.match(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\b",
                                   declaration, re.I) and
                          re.search(r"\bLANGUAGE\s+(?:sql|plpgsql)\b", declaration, re.I))
            if procedural:
                body_start = dollar.end()
                yield begin, body_start, False
                for a, b, allowed in _sql_regions(text[body_start:end]):
                    yield body_start + a, body_start + b, allowed
                yield end, i, False
            else:
                yield begin, i, False
            statement.append(" body ")
            continue
        if text[i] in "'\"":
            quote = text[i]
            escaped = quote == "'" and bool(re.search(r"(?:^|[^a-zA-Z_0-9])E$", "".join(statement), re.I))
            metadata = quote == "'" and bool(re.match(r"^\s*COMMENT\s+ON\b", "".join(statement), re.I))
            i += 1
            while i < len(text):
                if escaped and text[i] == "\\":
                    i += 2
                elif text[i] == quote:
                    i += 1
                    if i < len(text) and text[i] == quote:
                        i += 1
                    else:
                        break
                else:
                    i += 1
            else:
                raise ValueError("unterminated quote")
            yield begin, i, metadata
            statement.append(" literal ")
            continue
        # Accumulate statement text so COMMENT and COPY are recognized only
        # at SQL statement scope, never in strings or another COPY block.
        i += 1
        if text[begin] == ";":
            statement = []
        else:
            statement.append(text[begin])
        yield begin, i, False


def sanitize_snapshot(text, corpus):
    """Project permitted prose; refuse identities in executable or sealed data."""
    projected = []
    untouched_start = 0
    for begin, end, allowed in _sql_regions(text):
        if allowed:
            projected.append(text[untouched_start:begin])
            projected.append(synthetic_prose(text[begin:end], corpus))
            untouched_start = end
    projected.append(text[untouched_start:])
    result = "".join(projected)
    if identity_spans(result, corpus):
        raise ValueError("snapshot identity outside permitted prose")
    return result
