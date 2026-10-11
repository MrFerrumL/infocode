"""Local preview / WSGI server. For production use an HTTPS reverse proxy."""
import os
from waitress import serve


def server_options():
    options = dict(host=os.environ.get('BIND_HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8000')),
                   threads=int(os.environ.get('WEB_THREADS', '8')), max_request_body_size=525_000_000)
    proxy = os.environ.get('TRUSTED_PROXY')
    if proxy:
        options.update(trusted_proxy=proxy, trusted_proxy_count=1,
                       trusted_proxy_headers={'x-forwarded-for', 'x-forwarded-proto'})
    return options

if __name__ == '__main__':
    from config.wsgi import application
    options = server_options()
    print(f"Инфокод: http://{options['host']}:{options['port']}", flush=True)
    serve(application, **options)
