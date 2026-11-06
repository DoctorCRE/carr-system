"""Acquire zsh through signed Ubuntu packages and qualify the shebang path."""
import subprocess


def main():
    for arguments in (['update', '--error-on=any', '-qq'],
                      ['install', '-y', '-qq', '--no-install-recommends', 'zsh']):
        subprocess.run(['timeout', '--kill-after=5s', '180s', 'sudo', 'apt-get',
                        '-o', 'Acquire::Retries=0', '-o', 'Acquire::http::Timeout=15',
                        '-o', 'Acquire::https::Timeout=15', *arguments], check=True)
    subprocess.run(['/bin/zsh', '--version'], check=True, timeout=10)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
