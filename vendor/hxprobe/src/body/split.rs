use ego_tree::iter::Edge;
use scraper::{node::Element, Html, Node};

use super::{fold_lines, fold_spaces, parse_budget, BLOCKS};

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

#[derive(Clone, Copy, PartialEq, Eq)]
enum Scope {
    Visible,
    History,
    Hidden,
}

pub fn split_html(html: &str) -> Option<BodySplit> {
    if let Err(limit) = parse_budget::check(html.as_bytes()) {
        eprintln!("hxprobe: skipped HTML body split ({limit}); kept full text");
        return None;
    }

    let document = Html::parse_document(html);
    let mut split = Split::default();
    let mut scopes = vec![Scope::Visible];
    for edge in document.root_element().traverse() {
        match edge {
            Edge::Open(node) => {
                let parent = *scopes.last().expect("root scope");
                match node.value() {
                    Node::Element(element) => {
                        split.separate(element.name(), parent, true);
                        scopes.push(split.classify(element, parent));
                    }
                    Node::Text(text) => split.text(text, parent),
                    _ => {}
                }
            }
            Edge::Close(node) => {
                if let Node::Element(element) = node.value() {
                    let part = scopes.pop().expect("open element");
                    split.separate(element.name(), part, false);
                }
            }
        }
    }

    split.break_line();
    Some(BodySplit {
        authored: fold_lines(&split.authored),
        history: fold_lines(&split.history),
    })
}

#[derive(Default)]
struct Split {
    awaiting_quote: bool,
    authored_line: String,
    history_line: String,
    authored: String,
    history: String,
    after_caution: bool,
}

impl Split {
    fn separate(&mut self, name: &str, scope: Scope, opening: bool) {
        if scope != Scope::Hidden
            && ((opening && name == "br") || (is_block(name) && !self.line_is_empty()))
        {
            self.break_line();
        }
    }

    fn classify(&mut self, element: &Element, parent: Scope) -> Scope {
        let name = element.name();
        if matches!(name, "head" | "script" | "style" | "template" | "title") {
            return Scope::Hidden;
        }
        if parent != Scope::Visible {
            return parent;
        }
        if name == "blockquote" || marked(element, QUOTE_MARKERS) {
            self.awaiting_quote = false;
        } else if marked(element, &["divrplyfwdmsg"]) {
            // A bounded quote can end this history even inside the header wrapper.
            self.awaiting_quote = true;
            return Scope::Visible;
        } else if name != "footer"
            && !marked(element, HISTORY_MARKERS)
            && element.attr("data-smartmail") != Some("gmail_signature")
        {
            return Scope::Visible;
        }
        Scope::History
    }

    fn text(&mut self, text: &str, scope: Scope) {
        let line = match scope {
            Scope::Hidden => return,
            Scope::Visible if !self.awaiting_quote => &mut self.authored_line,
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

fn marked(element: &Element, markers: &[&str]) -> bool {
    element
        .id()
        .into_iter()
        .chain(element.classes())
        .any(|value| {
            let value = value.strip_prefix("x_").unwrap_or(value);
            markers
                .iter()
                .any(|marker| value.eq_ignore_ascii_case(marker))
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
        let split = split_html(html).expect("HTML within parse budget");
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
    fn postscript_inside_outlook_header_follows_its_bounded_quote() {
        assert_split(
            "<p>New note.</p><div id=divRplyFwdMsg>From: Sam Example\
             <blockquote>Old note.</blockquote><p>P.S. New instruction.</p></div>",
            "New note.\nP.S. New instruction.",
            "From: Sam Example\nOld note.",
        );
    }

    #[test]
    fn an_implicitly_closed_signature_cell_leaves_the_next_cell_authored() {
        assert_split(
            "<table><tr><td class=signature>Sam Example<td>Please send the plan.</table>",
            "Please send the plan.",
            "Sam Example",
        );
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
    fn deeply_nested_body_uses_the_plain_text_fallback() {
        let html = format!(
            "<div class=gmail_quote>{}Old note.{}</div><p>New note.</p>",
            "<div>".repeat(20_000),
            "</div>".repeat(20_000),
        );
        assert!(split_html(&html).is_none());
    }

    #[test]
    fn too_many_attributes_use_the_plain_text_fallback() {
        let attrs: String = (0..20_000).map(|n| format!(" a{n}=b")).collect();
        assert!(split_html(&format!("<div{attrs}>New note.</div>")).is_none());
    }

    #[test]
    fn attributes_on_repeated_body_tags_share_the_budget() {
        let html: String = (0..100).map(|n| format!("<body data-tag{n}=x>")).collect();
        assert!(split_html(&html).is_none());
    }

    #[test]
    fn oversized_body_uses_the_plain_text_fallback() {
        assert!(split_html(&"x".repeat(super::parse_budget::MAX_BYTES + 1)).is_none());
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
