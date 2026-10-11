"""Generate server-only secrets without displaying them or overwriting a config."""
import argparse
import os
from pathlib import Path
import re
import secrets


def generate(domain, email, target):
    domain = domain.strip().encode('idna').decode('ascii').lower()
    if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', domain):
        raise ValueError('Use a domain such as ege.example.ru, without https:// or a path.')
    if not re.fullmatch(r'[^\s@=]+@[^\s@=]+\.[^\s@=]+', email) or any(c in email for c in '#$"\''):
        raise ValueError('Enter a valid email for certificate notifications.')
    data = f'APP_DOMAIN={domain}\nACME_EMAIL={email}\nSECRET_KEY={secrets.token_urlsafe(64)}\nDB_PASSWORD={secrets.token_urlsafe(40)}\n'
    with os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as handle:
        handle.write(data)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain')
    parser.add_argument('--email')
    args = parser.parse_args()
    target = Path(__file__).resolve().parent.parent / '.env.production'
    try:
        generate(args.domain or input('Domain: '), args.email or input('Email: '), target)
    except (ValueError, OSError) as exc:
        parser.exit(1, f'{exc}\n')
    print('Created .env.production. Keep it on the server and in your private backup; do not commit it.')
