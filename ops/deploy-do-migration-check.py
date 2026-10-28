#!/usr/bin/env python3
"""Permit automatic full deploys only for additive migrations and matching domains."""
import json
import os
import sys
import tomllib
import urllib.error
import urllib.request


def check(config, applied, account, base):
    migrations = config.get('migrations', [])
    tags = [migration['tag'] for migration in migrations]
    if applied and applied not in tags:
        raise ValueError(f'production migration tag {applied!r} is not declared')
    pending = migrations[tags.index(applied) + 1:] if applied else migrations
    for migration in pending:
        unsafe = set(migration) - {'tag', 'new_classes', 'new_sqlite_classes'}
        if unsafe:
            raise ValueError(f'migration {migration["tag"]!r} is not additive only: {", ".join(sorted(unsafe))}')
    for setting in ('workers_dev', 'preview_urls'):
        if config.get(setting) is not False:
            raise ValueError(f'{setting} must be false')
    routes = config.get('routes', [])
    expected = {route['pattern'] for route in routes if isinstance(route, dict) and route.get('custom_domain') is True}
    if not expected:
        raise ValueError('wrangler.toml declares no custom domains')
    actual = set()
    page = 1
    while True:
        request = urllib.request.Request(f'{base}/accounts/{account}/workers/domains?page={page}&per_page=100', headers={'Authorization': 'Bearer ' + os.environ.get('CLOUDFLARE_API_TOKEN', '')})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.load(response)
        if data.get('success') is not True or not isinstance(data.get('result'), list):
            raise ValueError('Cloudflare domains API returned an unsuccessful or invalid response')
        for domain in data['result']:
            if not isinstance(domain, dict) or any(not isinstance(domain.get(key), str) or not domain[key] for key in ('service', 'environment', 'hostname')):
                raise ValueError('Cloudflare domains API returned a malformed domain')
            if domain['service'] == 'carr-mcp' and domain['environment'] == 'production':
                actual.add(domain['hostname'])
        info = data.get('result_info') or {}
        if 'total_pages' not in info and page == 1:
            break
        if info.get('page') != page or not isinstance(info.get('total_pages'), int) or info['total_pages'] < page:
            raise ValueError('Cloudflare domains API returned invalid pagination')
        if page == info['total_pages']:
            break
        page += 1
    if expected != actual:
        raise ValueError(f'custom domain mismatch; missing from production: {sorted(expected - actual)}; extra in production: {sorted(actual - expected)}')


def main():
    try:
        with open(sys.argv[1], 'rb') as file:
            config = tomllib.load(file)
        check(config, sys.argv[2], sys.argv[3], sys.argv[4])
    except (ValueError, KeyError, TypeError, OSError, urllib.error.URLError, json.JSONDecodeError, IndexError) as error:
        print(f'Durable Object migration automatic deploy refused: {error}', file=sys.stderr)
        return 1
    print('Additive Durable Object migrations and production custom domains verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
