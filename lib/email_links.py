"""Extract useful, untrusted URLs from decoded email text without fetching them."""
import re
from urllib.parse import parse_qs, unquote, urlsplit

URL = re.compile(r'https?://[^\s<>"\']+', re.I)
CONTROL = re.compile(r'[\x00-\x1f\x7f]')
MAX_LINKS = 20


def extract_links(text: str) -> list[tuple[str, str]]:
    found = []
    seen = set()
    for match in URL.finditer(text):
        url = match.group().rstrip('.,;!?)]')
        for _ in range(8):
            if CONTROL.search(unquote(url)):
                break
            try:
                parts = urlsplit(url)
                host = (parts.hostname or '').lower()
                if parts.scheme.lower() not in ('http', 'https') or not host or parts.username is not None or parts.password is not None:
                    break
                if host.endswith('.safelinks.protection.outlook.com'):
                    values = parse_qs(parts.query).get('url', [])
                    if len(values) != 1:
                        break
                    url = values[0]
                    for _ in range(4):
                        if re.match(r'https?://', url, re.I):
                            break
                        url = unquote(url)
                    continue
                if re.search(r'(?:^|\.)(?:loopnet\.com|costar\.com|crexi\.com)$', host):
                    kind = 'listing'
                elif re.search(r'(?:^|\.)(?:drive\.google\.com|docs\.google\.com|dropbox\.com|onedrive\.live\.com|sharepoint\.com|box\.com)$', host):
                    kind = 'shared_folder' if '/folders/' in parts.path.lower() else 'document'
                else:
                    break
                if url not in seen:
                    seen.add(url)
                    found.append((kind, url))
                break
            except ValueError:
                break
        if len(found) >= MAX_LINKS:
            break
    return found
