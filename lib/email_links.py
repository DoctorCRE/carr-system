"""Extract useful, untrusted URLs from decoded email text without fetching them."""
import html
import re
from urllib.parse import unquote, unquote_plus, urlsplit, urlunsplit

URL = re.compile(r'https?://[^\s<>"\']+', re.I)
CONTROL = re.compile(r'[\x00-\x1f\x7f]')
MAX_LINKS = 20


def unwrap_safe_links(text: str) -> str:
    """Recover HTTP destinations without changing unrelated text or malformed wrappers."""
    def replace(match: re.Match) -> str:
        original = match.group()
        token = original.rstrip('.,;!?)]')
        url = html.unescape(token)
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
                    if url == html.unescape(token):
                        return original
                    attribute = re.search(r'''=\s*["']?$''', text[:match.start()])
                    destination = html.escape(url, quote=True) if attribute or '&amp;' in token else url
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

    return URL.sub(replace, text)


def extract_links(text: str) -> list[tuple[str, str]]:
    found = []
    seen = set()
    text = unwrap_safe_links(text)
    for match in URL.finditer(text):
        url = match.group().rstrip('.,;!?)]')
        if re.search(r'''=\s*["']?$''', text[:match.start()]):
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
