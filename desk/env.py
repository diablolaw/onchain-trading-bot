"""Small, non-executable project .env loader; exported variables take priority."""
import os
from pathlib import Path


PROJECT_ENV = Path(__file__).resolve().parent.parent / '.env'
ALLOWED = {'TYPESAFE_API_KEY', 'XAI_API_KEY', 'JUPITER_API_KEY', 'SOLANA_RPC_URL'}


def load_env(path=PROJECT_ENV):
    path = Path(path)
    if not path.exists():
        return
    values = {}
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or key not in ALLOWED:
            raise ValueError(f'invalid .env assignment at line {line_number}')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'unclosed .env quote at line {line_number}')
            value = value[1:-1]
        values[key] = value
    # Parse the whole file before changing the process environment.
    for key, value in values.items():
        if value:
            os.environ.setdefault(key, value)
