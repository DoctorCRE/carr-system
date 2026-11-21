use std::{cell::RefCell, collections::HashMap};

use html5ever::tokenizer::{
    states::RawKind, BufferQueue, Tag, TagKind, Token, TokenSink, TokenSinkResult, Tokenizer,
};
use html5ever::LocalName;

use super::{fold_lines, fold_spaces, BLOCKS};

const QUOTE_MARKERS: &[&str] = &[
    "gmail_quote",
    "gmail_quote_container",
    "yahoo_quoted",
    "protonmail_quote",
];
const HISTORY_MARKERS: &[&str] = &[
    "gmail_attr",
    "moz-cite-prefix",
    "signature",
    "gmail_signature",
    "ms-outlook-mobile-signature",
    "applemailsignature",
    "moz-signature",
    "protonmail_signature",
    "footer",
    "mail-footer",
    "email-footer",
    "disclaimer",
];

pub struct BodySplit {
    pub authored: String,
    pub history: String,
}

#[derive(Clone, Copy, Default, PartialEq, Eq)]
enum Part {
    #[default]
    Authored,
    History,
    Hidden,
}

pub fn split_html(html: &str) -> BodySplit {
    let input = BufferQueue::default();
    input.push_back(html.into());
    let tokenizer = Tokenizer::new(Sink::default(), Default::default());
    let _ = tokenizer.feed(&input);
    tokenizer.end();
    let mut split = tokenizer.sink.0.into_inner();
    split.break_line();
    BodySplit {
        authored: fold_lines(&split.authored),
        history: fold_lines(&split.history),
    }
}

struct OpenTag {
    name: LocalName,
    previous: Option<usize>,
    part: Part,
}

#[derive(Default)]
struct Split {
    open: Vec<OpenTag>,
    last_open: HashMap<LocalName, usize>,
    awaiting_quote: bool,
    authored_line: String,
    history_line: String,
    authored: String,
    history: String,
    after_caution: bool,
}

#[derive(Default)]
struct Sink(RefCell<Split>);

impl TokenSink for Sink {
    type Handle = ();

    fn process_token(&self, token: Token, _: u64) -> TokenSinkResult<()> {
        let mut split = self.0.borrow_mut();
        match token {
            Token::TagToken(tag) => return split.tag(tag),
            Token::CharacterTokens(text) => split.text(&text),
            _ => {}
        }
        TokenSinkResult::Continue
    }
}

impl Split {
    fn scope(&self) -> Part {
        self.open.last().map_or(Part::Authored, |tag| tag.part)
    }

    fn tag(&mut self, tag: Tag) -> TokenSinkResult<()> {
        let name = tag.name.as_ref();
        if self.scope() != Part::Hidden {
            if name == "br" {
                self.break_line();
            } else if is_block(name) && !self.line_is_empty() {
                self.break_line();
            }
        }
        if tag.kind == TagKind::EndTag {
            self.close(&tag.name);
            return TokenSinkResult::Continue;
        }
        if name == "body" {
            self.close(&LocalName::from("head"));
        } else if self.scope() != Part::Hidden && is_block(name) {
            self.close(&LocalName::from("p"));
        }

        if matches!(
            name,
            "area"
                | "base"
                | "br"
                | "col"
                | "embed"
                | "hr"
                | "img"
                | "input"
                | "link"
                | "meta"
                | "param"
                | "source"
                | "track"
                | "wbr"
        ) {
            return TokenSinkResult::Continue;
        }

        let part = self.classify(&tag);
        let raw = match name {
            "script" => TokenSinkResult::RawData(RawKind::ScriptData),
            "style" | "xmp" | "iframe" | "noembed" | "noframes" => {
                TokenSinkResult::RawData(RawKind::Rawtext)
            }
            "title" | "textarea" => TokenSinkResult::RawData(RawKind::Rcdata),
            "plaintext" => TokenSinkResult::Plaintext,
            _ => TokenSinkResult::Continue,
        };
        if !tag.self_closing || raw != TokenSinkResult::Continue {
            let previous = self.last_open.insert(tag.name.clone(), self.open.len());
            self.open.push(OpenTag {
                name: tag.name,
                previous,
                part,
            });
        }
        raw
    }

    fn classify(&mut self, tag: &Tag) -> Part {
        let name = tag.name.as_ref();
        if matches!(name, "head" | "script" | "style" | "template" | "title") {
            return Part::Hidden;
        }
        let inherited = self.scope();
        if inherited != Part::Authored {
            return inherited;
        }
        if name == "blockquote" || marked(tag, QUOTE_MARKERS) {
            self.awaiting_quote = false;
        } else if marked(tag, &["divrplyfwdmsg"]) {
            self.awaiting_quote = true;
        } else if name != "footer"
            && !marked(tag, HISTORY_MARKERS)
            && !tag.attrs.iter().any(|attr| {
                &*attr.name.local == "data-smartmail" && attr.value.as_ref() == "gmail_signature"
            })
        {
            return Part::Authored;
        }
        Part::History
    }

    fn close(&mut self, name: &LocalName) {
        // Index closing tags so a stray end tag never scans all its ancestors.
        if let Some(&index) = self.last_open.get(name) {
            for tag in self.open.drain(index..).rev() {
                if let Some(previous) = tag.previous {
                    self.last_open.insert(tag.name, previous);
                } else {
                    self.last_open.remove(&tag.name);
                }
            }
        }
    }

    fn text(&mut self, text: &str) {
        let line = match self.scope() {
            Part::Hidden => return,
            Part::Authored if !self.awaiting_quote => &mut self.authored_line,
            _ => &mut self.history_line,
        };
        for c in text.chars() {
            if c.is_ascii_whitespace() {
                if !line.is_empty() && !line.ends_with(' ') {
                    line.push(' ');
                }
            } else if !c.is_control() {
                line.push(c);
            }
        }
    }

    fn line_is_empty(&self) -> bool {
        self.authored_line.is_empty() && self.history_line.is_empty()
    }

    fn break_line(&mut self) {
        let authored = self.authored_line.trim_end();
        let history = self.history_line.trim_end();
        if authored.is_empty() && history.is_empty() {
            self.authored.push('\n');
            self.history.push('\n');
            self.after_caution = false;
        } else {
            let banner =
                history.is_empty() && is_external_banner(authored, &mut self.after_caution);
            let target = if banner {
                &mut self.history
            } else {
                &mut self.authored
            };
            if !authored.is_empty() {
                target.push_str(authored);
                target.push('\n');
            }
            if !history.is_empty() {
                self.history.push_str(history);
                self.history.push('\n');
                self.after_caution = false;
            }
        }
        self.authored_line.clear();
        self.history_line.clear();
    }
}

fn is_block(name: &str) -> bool {
    BLOCKS.contains(&name)
        || matches!(
            name,
            "h4" | "h5" | "h6" | "footer" | "section" | "table" | "ul" | "ol" | "th"
        )
}

fn marked(tag: &Tag, markers: &[&str]) -> bool {
    tag.attrs.iter().any(|attr| {
        let value = attr.value.as_ref();
        let matches_marker = |value: &str| {
            let value = value.strip_prefix("x_").unwrap_or(value);
            markers
                .iter()
                .any(|marker| value.eq_ignore_ascii_case(marker))
        };
        match attr.name.local.as_ref() {
            "id" => matches_marker(value),
            "class" => value.split_ascii_whitespace().any(matches_marker),
            _ => false,
        }
    })
}

fn is_external_banner(line: &str, after_caution: &mut bool) -> bool {
    const CAUTION: &str = "this email originated from outside of the organization.";
    const WARNINGS: &[&str] = &[
        "do not click links if unexpected.",
        "do not click links if you do not recognize the sender.",
        "do not click links or open attachments unless you recognize the sender and know the content is safe.",
    ];
    let continuation = std::mem::take(after_caution);
    if line.len() > 1024 {
        return false;
    }
    let line = fold_spaces(line).to_ascii_lowercase();
    let caution = line.strip_prefix("caution: ").unwrap_or(&line);
    *after_caution = caution == CAUTION;
    *after_caution
        || (continuation && WARNINGS.contains(&line.as_str()))
        || caution
            .strip_prefix(CAUTION)
            .and_then(|rest| rest.strip_prefix(' '))
            .is_some_and(|warning| WARNINGS.contains(&warning))
        || ["you", "some people who received this message"]
            .iter()
            .any(|prefix| {
                line.strip_prefix(prefix)
                    .and_then(|rest| rest.strip_prefix(" don't often get email from "))
                    .and_then(|rest| rest.strip_suffix(" learn why this is important"))
                    .is_some_and(|sender| {
                        sender == "this sender" || (sender.contains('@') && !sender.contains(' '))
                    })
            })
}

#[cfg(test)]
mod tests {
    use super::split_html;

    fn assert_split(html: &str, authored: &str, history: &str) {
        let split = split_html(html);
        assert_eq!(split.authored, authored, "authored");
        assert_eq!(split.history, history, "history");
    }

    #[test]
    fn outlook_header_followed_by_bounded_quote_keeps_the_postscript() {
        for (header, quote) in [
            (
                "<div id=divRplyFwdMsg>From: Sam Example</div>",
                "<blockquote>Old note.</blockquote>",
            ),
            (
                "<div><div id=divRplyFwdMsg>From: Sam Example</div></div>",
                "<div class=gmail_quote><div>Old note.</div></div>",
            ),
        ] {
            let html = format!("<p>New note.</p>{header}{quote}<p>P.S. New instruction.</p>");
            assert_split(
                &html,
                "New note.\nP.S. New instruction.",
                "From: Sam Example\nOld note.",
            );
        }
    }

    #[test]
    fn outlook_header_without_bounded_quote_extends_past_its_wrapper() {
        assert_split(
        "<p>New note.</p><div><div id=divRplyFwdMsg>From: Sam Example</div></div><p>Old note.</p>",
        "New note.",
        "From: Sam Example\nOld note.",
    );
    }

    #[test]
    fn caution_banner_leaves_other_lines_in_the_same_block_authored() {
        assert_split(
        "<div>Caution: This email originated from outside of the organization.<br>\
         Do not click links if unexpected.<br>Please send the plan.</div>",
        "Please send the plan.",
        "Caution: This email originated from outside of the organization.\nDo not click links if unexpected.",
    );
    }

    #[test]
    fn caution_banner_prefix_does_not_hide_a_sender_paragraph() {
        let paragraph = "Caution: This email originated from outside of the organization. \
                     Do not click links in the draft plan until we meet. Please send the plan.";
        assert_split(&format!("<p>{paragraph}</p>"), paragraph, "");
    }

    #[test]
    fn separated_signatures_keep_their_breaks() {
        assert_split(
        "<span class=gmail_signature>First</span><br><span class=signature>Second</span><p>New note.</p>",
        "New note.",
        "First\nSecond",
    );
        assert_split(
        "<span class=gmail_signature>First</span><br><br><span class=signature>Second</span><p>New note.</p>",
        "New note.",
        "First\n\nSecond",
    );
    }

    #[test]
    fn adjacent_headings_keep_their_breaks() {
        assert_split(
            "<h4>First</h4><h4>Second</h4><h5>Third</h5><h6>Fourth</h6>",
            "First\nSecond\nThird\nFourth",
            "",
        );
    }

    #[test]
    fn deeply_nested_body_finishes_within_the_time_bound() {
        use std::time::{Duration, Instant};

        let html = format!(
            "<div class=gmail_quote>{}Old note.{}</div><p>New note.</p>",
            "<div>".repeat(20_000),
            "</div>".repeat(20_000),
        );
        let start = Instant::now();
        assert_split(&html, "New note.", "Old note.");
        let elapsed = start.elapsed();
        assert!(elapsed < Duration::from_secs(2), "split took {elapsed:?}");
    }

    #[test]
    fn large_body_keeps_all_lines_in_order() {
        let section = "<div>New note: Caf&eacute;.</div><blockquote>Old note.</blockquote>";
        assert_split(
            &section.repeat(25_000),
            &"New note: Café.\n".repeat(25_000).trim_end(),
            &"Old note.\n".repeat(25_000).trim_end(),
        );
    }
}
