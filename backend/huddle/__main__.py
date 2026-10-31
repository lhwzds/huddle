"""`python -m huddle`: serve the API on $HOST:$PORT (defaults 0.0.0.0:8000)."""
import os

import uvicorn


def _port(value):
    try:
        port = int(value)
    except ValueError:
        raise SystemExit(f'PORT must be a number, got {value!r}') from None
    if not 1 <= port <= 65535:
        raise SystemExit(f'PORT must be between 1 and 65535, got {port}')
    return port


if __name__ == '__main__':
    uvicorn.run('huddle.main:create_app', factory=True,
                host=os.environ.get('HOST') or '0.0.0.0',
                port=_port(os.environ.get('PORT') or '8000'))
