"""Extract useful, untrusted URLs from decoded email text without fetching them."""
import html
import re
from html.parser import HTMLParser
from urllib.parse import unquote, unquote_plus, urlsplit, urlunsplit

URL = re.compile(r'https?://[^\s<>"\']+', re.I)
CONTROL = re.compile(r'[\x00-\x1f\x7f]')
MAX_LINKS = 20


class _HTMLTags(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.offsets = [0]
        for match in re.finditer('\n', text):
            self.offsets.append(match.end())
        self.spans: list[tuple[int, int]] = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        line, column = self.getpos()
        start = self.offsets[line - 1] + column
        self.spans.append((start, start + len(self.get_starttag_text())))


def _url_matches(text: str):
    spans = _HTMLTags(text).spans
    for match in URL.finditer(text):
        attribute = any(start <= match.start() < end for start, end in spans)
        yield match, attribute


def _unwrap_url(original: str, attribute: bool) -> str:
    token = original if attribute else original.rstrip('.,;!?)]')
    initial = html.unescape(token) if attribute else token
    url = initial
    for _ in range(8):
        try:
            parts = urlsplit(url)
            host = (parts.hostname or '').lower()
            if (parts.scheme.lower() not in ('http', 'https') or not host
                    or not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]+)?', parts.netloc)
                    or CONTROL.search(unquote(url)) or '\\' in unquote(url)
                    or re.search(r'%(?![0-9a-fA-F]{2})', url)
                    or re.search(r'''[\s<>"']''', url)):
                return original
            if not re.fullmatch(r'(?:[a-z0-9-]+\.)+safelinks\.protection\.outlook\.com', host):
                if url == initial:
                    return original
                destination = html.escape(url, quote=True) if attribute else url
                return destination + original[len(token):]
            values = [value for field in parts.query.split('&')
                      for name, _, value in [field.partition('=')] if name == 'url']
            if len(values) != 1:
                return original
            url = unquote_plus(values[0], errors='strict')
            for _ in range(4):
                if re.match(r'https?://', url, re.I):
                    break
                url = unquote(url, errors='strict')
        except (ValueError, UnicodeError):
            return original
    return original


def unwrap_safe_links(text: str) -> str:
    """Recover HTTP destinations without changing unrelated text or malformed wrappers."""
    chunks: list[str] = []
    end = 0
    for match, attribute in _url_matches(text):
        chunks.extend((text[end:match.start()], _unwrap_url(match.group(), attribute)))
        end = match.end()
    chunks.append(text[end:])
    return ''.join(chunks)


def extract_links(text: str) -> list[tuple[str, str]]:
    found = []
    seen = set()
    for match, attribute in _url_matches(text):
        token = match.group() if attribute else match.group().rstrip('.,;!?)]')
        url = _unwrap_url(token, attribute)
        if attribute:
            url = html.unescape(url)
        if CONTROL.search(unquote(url)) or '\\' in unquote(url):
            continue
        try:
            parts = urlsplit(url)
            host = (parts.hostname or '').lower()
            if (parts.scheme.lower() not in ('http', 'https') or not host
                    or not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]+)?', parts.netloc)
                    or parts.username is not None or parts.password is not None):
                continue
            if re.search(r'(?:^|\.)(?:loopnet\.com|costar\.com|crexi\.com)$', host):
                kind = 'listing'
            elif re.search(r'(?:^|\.)(?:drive\.google\.com|docs\.google\.com|dropbox\.com|onedrive\.live\.com|sharepoint\.com|box\.com)$', host):
                kind = 'shared_folder' if '/folders/' in parts.path.lower() else 'document'
            else:
                continue
            url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment))
            if url not in seen:
                seen.add(url)
                found.append((kind, url))
        except ValueError:
            continue
        if len(found) >= MAX_LINKS:
            break
    return found
