use ego_tree::{iter::Edge, NodeRef};
use scraper::{node::Element, Html, Node};

use super::{fold_lines, fold_spaces, BLOCKS};

pub struct BodySplit {
    pub authored: String,
    pub history: String,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Part {
    Authored,
    History,
    Hidden,
}

pub fn split_html(html: &str) -> BodySplit {
    let document = Html::parse_document(html);
    let mut scopes = vec![Part::Authored];
    let mut authored = Text::default();
    let mut history = Text::default();

    for edge in document.root_element().traverse() {
        let parent = scopes.last_mut().expect("root scope");
        let (part, block, line, text) = match edge {
            Edge::Open(node) => match node.value() {
                Node::Element(element) => {
                    let name = element.name();
                    let mut part = *parent;
                    if matches!(name, "head" | "script" | "style" | "template") {
                        part = Part::Hidden;
                    } else if part == Part::Authored {
                        // Outlook's header introduces the rest of its enclosing section.
                        if element_matches(element, &["divrplyfwdmsg"]) {
                            *parent = Part::History;
                            part = Part::History;
                        } else if is_history(node, element) {
                            part = Part::History;
                        }
                        if part == Part::History && is_block(name) {
                            authored.block_break();
                        }
                    }
                    scopes.push(part);
                    (part, is_block(name), name == "br", None)
                }
                Node::Text(text) => (*parent, false, false, Some(&**text)),
                _ => continue,
            },
            Edge::Close(node) => {
                let Node::Element(element) = node.value() else {
                    continue;
                };
                let part = scopes.pop().expect("scope for open element");
                (part, is_block(element.name()), false, None)
            }
        };

        let output = match part {
            Part::Authored => &mut authored,
            Part::History => &mut history,
            Part::Hidden => continue,
        };
        if line {
            output.line_break();
        } else if block {
            output.block_break();
        }
        if let Some(text) = text {
            output.push(text);
        }
    }

    BodySplit {
        authored: fold_lines(&authored.raw),
        history: fold_lines(&history.raw),
    }
}

fn is_block(name: &str) -> bool {
    BLOCKS.contains(&name) || matches!(name, "footer" | "section" | "table" | "ul" | "ol" | "th")
}

fn element_matches(element: &Element, names: &[&str]) -> bool {
    element
        .id()
        .into_iter()
        .chain(element.classes())
        .any(|value| {
            let value = value.strip_prefix("x_").unwrap_or(value);
            names.iter().any(|name| value.eq_ignore_ascii_case(name))
        })
}

fn is_history(node: NodeRef<'_, Node>, element: &Element) -> bool {
    matches!(element.name(), "blockquote" | "footer")
        || element_matches(
            element,
            &[
                "gmail_quote",
                "gmail_quote_container",
                "gmail_attr",
                "yahoo_quoted",
                "protonmail_quote",
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
                "external-sender-banner",
            ],
        )
        || element.attr("data-smartmail") == Some("gmail_signature")
        || is_external_banner(node, element.name())
}

fn is_external_banner(node: NodeRef<'_, Node>, name: &str) -> bool {
    if !matches!(name, "div" | "p" | "td") {
        return false;
    }
    let mut text = String::new();
    for child in node.descendants().skip(1) {
        match child.value() {
            Node::Element(element) if is_block(element.name()) => return false,
            Node::Text(content) => {
                if text.len() + content.len() > 1024 {
                    return false;
                }
                text.push_str(content);
            }
            Node::Element(element) if element.name() == "br" => text.push(' '),
            _ => {}
        }
    }
    let text = fold_spaces(&text).to_ascii_lowercase();
    let caution = text.strip_prefix("caution: ").unwrap_or(&text);
    caution
        .strip_prefix("this email originated from outside of the organization.")
        .is_some_and(|rest| rest.is_empty() || rest.starts_with(" do not click links "))
        || ["you", "some people who received this message"]
            .iter()
            .filter_map(|start| text.strip_prefix(start))
            .filter_map(|rest| rest.strip_prefix(" don't often get email from "))
            .any(|rest| {
                rest.strip_suffix(" learn why this is important")
                    .is_some_and(|sender| {
                        sender == "this sender" || (sender.contains('@') && !sender.contains(' '))
                    })
            })
}

#[derive(Default)]
struct Text {
    raw: String,
    line_has_text: bool,
}

impl Text {
    fn push(&mut self, text: &str) {
        for c in text.chars() {
            if c.is_ascii_whitespace() {
                self.raw.push(' ');
            } else if !c.is_control() {
                self.raw.push(c);
                self.line_has_text = true;
            }
        }
    }

    fn line_break(&mut self) {
        self.raw.push('\n');
        self.line_has_text = false;
    }

    fn block_break(&mut self) {
        if self.line_has_text {
            self.line_break();
        }
    }
}
