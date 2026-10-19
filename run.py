"""Native Windows entry point. Does not require Docker or PostgreSQL."""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def configure():
    os.chdir(ROOT)
    config = ROOT / '.env'
    if not config.exists():
        config.write_text((ROOT / '.env.example').read_text(encoding='utf-8'), encoding='utf-8')
    for line in config.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not key.strip().replace('_', '').isalnum():
            raise ValueError('Invalid .env line; expected KEY=value')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ['"', "'"]:
            value = value[1:-1]
        if value:
            os.environ.setdefault(key.strip(), value)
    (ROOT / 'data').mkdir(exist_ok=True)
    os.environ.setdefault('DATABASE_URL', 'sqlite:///' + (ROOT / 'data' / 'hr.db').as_posix())
    os.environ.setdefault('COOKIE_SECURE', 'false')
    os.environ.setdefault('RAGFLOW_URL', 'https://api.swipies.app')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', action='store_true', help='Create missing demo accounts and display their passwords')
    parser.add_argument('--check', action='store_true', help='Check database and frontend without starting a server')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    configure()
    if args.seed:
        from app.seed import seed
        seed()
        return
    if args.check:
        from app.main import Base, engine
        from sqlalchemy import select
        Base.metadata.create_all(engine)
        with engine.connect() as connection:
            connection.execute(select(1))
        assert (ROOT / 'app' / 'static' / 'index.html').is_file()
        print('OK: database, application and frontend. RAGFlow credentials are not checked.')
        return
    import uvicorn
    print(f'DOMPLAN HR: http://127.0.0.1:{args.port} | Stop: Ctrl+C')
    uvicorn.run('app.main:app', host='127.0.0.1', port=args.port)

if __name__ == '__main__':
    main()
