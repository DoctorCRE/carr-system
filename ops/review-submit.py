"""Submit a review bound to the head the reviewer actually inspected."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import time
import urllib.error
import urllib.request

import jwt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.github_app_config import CONFIG_PATH, read_config

CONFIG = read_config(CONFIG_PATH)
API = 'https://api.github.com'


def private_key():
    path = Path.home() / '.config/carr' / CONFIG['key_file']
    try:
        with path.open('rb') as source:
            if stat.S_IMODE(os.fstat(source.fileno()).st_mode) & 0o077:
                raise ValueError('GitHub App key permissions must be 0600')
            return source.read()
    except OSError:
        raise ValueError('GitHub App private key unavailable') from None


def request(path, bearer, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=body, headers={
        'Authorization': 'Bearer ' + bearer, 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'carr-review',
        'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except (OSError, ValueError):
        raise ValueError('GitHub App API request failed') from None


def installation_token():
    try:
        now = int(time.time())
        signed = jwt.encode({'iat': now - 60, 'exp': now + 540, 'iss': str(CONFIG['app_id'])}, private_key(), algorithm='RS256')
        installation = request('/repos/' + CONFIG['repository'] + '/installation', signed)
        token = request('/app/installations/' + str(installation['id']) + '/access_tokens', signed, {})['token']
        if not isinstance(token, str) or not token:
            raise ValueError
        return token
    except (KeyError, TypeError, jwt.PyJWTError):
        raise ValueError('GitHub App authentication failed') from None


def submit(number, decision, body, reviewed_sha):
    token = installation_token()
    path = f"/repos/{CONFIG['repository']}/pulls/{number}"
    if request(path, token)['head']['sha'] != reviewed_sha:
        raise ValueError('PR head moved; review not submitted')
    request(path + '/reviews', token, {'event': {'approve': 'APPROVE', 'request-changes': 'REQUEST_CHANGES'}[decision], 'body': body, 'commit_id': reviewed_sha})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('number', type=int)
    parser.add_argument('decision', choices=['approve', 'request-changes'])
    parser.add_argument('--body-file', required=True, type=Path)
    parser.add_argument('--reviewed-sha', required=True, help='full SHA actually reviewed')
    args = parser.parse_args()
    if args.number < 1 or len(args.reviewed_sha) != 40 or any(c not in '0123456789abcdef' for c in args.reviewed_sha):
        parser.error('provide a positive PR number and full lowercase reviewed SHA')
    try:
        body = args.body_file.read_text()
        if not body.strip():
            raise ValueError('review body is empty')
        submit(args.number, args.decision, body, args.reviewed_sha)
    except (OSError, ValueError, KeyError):
        raise SystemExit('Review not submitted: App authentication, input, or GitHub request failed') from None
    print(f'Review submitted for PR {args.number} at {args.reviewed_sha}')


if __name__ == '__main__':
    main()
