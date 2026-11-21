use memchr::{memchr, memchr_iter};

pub(super) const MAX_BYTES: usize = 2 << 20;
const MAX_DEPTH: usize = 128;
const MAX_ATTRIBUTES: usize = 64;

const RAW_TEXT: &[&[u8]] = &[
    b"style",
    b"script",
    b"title",
    b"textarea",
    b"xmp",
    b"iframe",
    b"noembed",
    b"noframes",
    b"noscript",
    b"plaintext",
];
const VOID: &[&[u8]] = &[
    b"area", b"base", b"br", b"col", b"embed", b"hr", b"img", b"input", b"link", b"meta", b"param",
    b"source", b"track", b"wbr",
];

pub(super) fn check(mut input: &[u8]) -> Result<(), &'static str> {
    if input.len() > MAX_BYTES {
        return Err("body size");
    }

    let mut open: Vec<&[u8]> = Vec::new();
    let (mut html_attrs, mut body_attrs) = (0, 0);
    while let Some(start) = memchr(b'<', input) {
        input = &input[start + 1..];
        if let Some(comment) = input.strip_prefix(b"!--") {
            input = after_comment(comment);
            continue;
        }

        let closing = input.first() == Some(&b'/');
        let tag = if closing { &input[1..] } else { input };
        if !tag.first().is_some_and(u8::is_ascii_alphabetic) {
            if input.starts_with(b"![CDATA[") {
                return Err("ambiguous CDATA");
            }
            if closing || matches!(input.first(), Some(b'!' | b'?')) {
                input = memchr(b'>', input).map_or(&[], |end| &input[end + 1..]);
            }
            continue;
        }

        let attributes =
            from_delimiter(tag, |c| c.is_ascii_whitespace() || matches!(c, b'/' | b'>'));
        let name = &tag[..tag.len() - attributes.len()];
        let (rest, attrs) = after_tag(attributes)?;
        input = rest;
        if closing {
            // Unmatched closes cannot safely release any of the source nesting budget.
            if open
                .last()
                .is_some_and(|last| last.eq_ignore_ascii_case(name))
            {
                open.pop();
            }
            continue;
        }
        // The tree builder merges repeated document tags into the same elements.
        if name.eq_ignore_ascii_case(b"html") {
            html_attrs += attrs;
        } else if name.eq_ignore_ascii_case(b"body") {
            body_attrs += attrs;
        }
        if html_attrs > MAX_ATTRIBUTES || body_attrs > MAX_ATTRIBUTES {
            return Err("attributes per tag");
        }

        let foreign = open.iter().any(|tag| is_name(tag, &[b"svg", b"math"]));
        let raw_text = is_name(name, RAW_TEXT);
        if (raw_text || foreign || !is_name(name, VOID)) && open.len() == MAX_DEPTH {
            return Err("source nesting depth");
        }
        if raw_text {
            if foreign
                || open
                    .iter()
                    .any(|tag| is_name(tag, &[b"select", b"frameset"]))
            {
                return Err("ambiguous raw text");
            }
            if name.eq_ignore_ascii_case(b"plaintext") {
                break;
            }
            input = after_raw_text(input, name)?;
        } else if foreign || !is_name(name, VOID) {
            open.push(name);
        }
    }
    Ok(())
}

fn is_name(name: &[u8], candidates: &[&[u8]]) -> bool {
    candidates
        .iter()
        .any(|candidate| name.eq_ignore_ascii_case(candidate))
}

fn after_comment(input: &[u8]) -> &[u8] {
    if let Some(rest) = input
        .strip_prefix(b">")
        .or_else(|| input.strip_prefix(b"->"))
    {
        return rest;
    }

    memchr_iter(b'>', input)
        .find(|&end| input[..end].ends_with(b"--") || input[..end].ends_with(b"--!"))
        .map_or(&[], |end| &input[end + 1..])
}

fn after_raw_text<'a>(mut input: &'a [u8], name: &[u8]) -> Result<&'a [u8], &'static str> {
    while let Some(start) = memchr(b'<', input) {
        input = &input[start + 1..];
        if name.eq_ignore_ascii_case(b"script") && input.starts_with(b"!--") {
            return Err("script markup");
        }
        if let Some(end) = input.strip_prefix(b"/") {
            if end
                .get(..name.len())
                .is_some_and(|tag| tag.eq_ignore_ascii_case(name))
                && end
                    .get(name.len())
                    .is_some_and(|c| c.is_ascii_whitespace() || matches!(c, b'/' | b'>'))
            {
                return after_tag(&end[name.len()..]).map(|(rest, _)| rest);
            }
        }
    }
    Ok(&[])
}

fn after_tag(mut input: &[u8]) -> Result<(&[u8], usize), &'static str> {
    let mut attributes = 0;
    while let Some((&next, tail)) = input.trim_ascii_start().split_first() {
        match next {
            b'>' => return Ok((tail, attributes)),
            b'/' => input = tail,
            _ => {
                attributes += 1;
                if attributes > MAX_ATTRIBUTES {
                    return Err("attributes per tag");
                }
                input = from_delimiter(tail, |c| {
                    c.is_ascii_whitespace() || matches!(c, b'=' | b'/' | b'>')
                })
                .trim_ascii_start();
                if let Some(value) = input.strip_prefix(b"=") {
                    input = after_attribute_value(value.trim_ascii_start());
                }
            }
        }
    }
    Ok((&[], attributes))
}

fn after_attribute_value(input: &[u8]) -> &[u8] {
    match input.split_first() {
        Some((&quote @ (b'\'' | b'"'), rest)) => {
            memchr(quote, rest).map_or(&[], |end| &rest[end + 1..])
        }
        _ => from_delimiter(input, |c| c.is_ascii_whitespace() || c == b'>'),
    }
}

fn from_delimiter(input: &[u8], delimiter: impl Fn(u8) -> bool) -> &[u8] {
    input
        .iter()
        .position(|&c| delimiter(c))
        .map_or(&[], |end| &input[end..])
}
