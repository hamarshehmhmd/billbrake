from __future__ import annotations

import argparse
import json

from . import __version__
from .config import load
from .store import Store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="billbrake", description="Hard spend caps for LLM APIs.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the capping proxy")
    s.add_argument("-c", "--config", default="billbrake.yaml")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    st = sub.add_parser("status", help="print spend vs caps")
    st.add_argument("-c", "--config", default="billbrake.yaml")
    r = sub.add_parser("reset", help="clear recorded spend for a project")
    r.add_argument("project")
    r.add_argument("--period", help="YYYY-MM-DD or YYYY-MM; default all")
    r.add_argument("-c", "--config", default="billbrake.yaml")
    a = ap.parse_args(argv)
    cfg = load(a.config)
    if a.cmd == "serve":
        import uvicorn

        from .proxy import create_app
        uvicorn.run(create_app(cfg), host=a.host, port=a.port)
    elif a.cmd == "status":
        from .proxy import status_snapshot
        print(json.dumps(status_snapshot(cfg, Store(cfg.db_path)), indent=2))
    elif a.cmd == "reset":
        Store(cfg.db_path).reset(a.project, a.period)
        print(f"reset {a.project} {a.period or '(all periods)'}")


if __name__ == "__main__":
    main()
