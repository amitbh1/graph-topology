"""MedGraph command line.

    python -m medgraph seed                 # offline illustrative sample graph
    python -m medgraph etl all              # full public-data ETL (or individual steps)
    python -m medgraph validate             # ontology vs code vs database
    python -m medgraph stats
    python -m medgraph ask "What does MedGraph know about Glucotrol?"
    python -m medgraph chat
    python -m medgraph serve
    python -m medgraph apikey alice --scopes read,chat   # prints a new key + its config entry
    python -m medgraph check-config                      # production readiness of the configuration
    python -m medgraph eval                              # behavioural evals of the assistant
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="medgraph")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schema", help="create constraints and indexes from the ontology")
    s = sub.add_parser("seed", help="load the offline illustrative sample graph")
    s.add_argument("--no-embed", action="store_true")
    e = sub.add_parser("etl", help="run ETL steps")
    e.add_argument("steps", nargs="+", help="all | schema atc mesh labels rxnorm atc-link faers trials icd10 similarity embed")
    sub.add_parser("validate", help="validate ontology against code and database")
    sub.add_parser("stats", help="graph statistics")
    pr = sub.add_parser("profile", help="full drug profile (e.g. glucotrol)")
    pr.add_argument("drug")
    a = sub.add_parser("ask", help="ask the assistant one question")
    a.add_argument("question")
    sub.add_parser("chat", help="interactive assistant")
    sv = sub.add_parser("serve", help="run API + dashboard")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--workers", type=int, default=1)
    k = sub.add_parser("apikey", help="generate an API key and its MEDGRAPH_API_KEYS entry")
    k.add_argument("name")
    k.add_argument("--scopes", default="read,chat", help="comma-separated: read, chat, admin")
    sub.add_parser("check-config", help="list unsafe or invalid configuration values")
    sub.add_parser("eval", help="run the assistant against evals/cases.json (needs graph + LLM)")
    args = p.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("medgraph.api.main:app", host=args.host, port=args.port, workers=args.workers,
                    proxy_headers=False, server_header=False, timeout_keep_alive=5)
        return 0

    if args.cmd == "apikey":
        return _apikey(args.name, args.scopes)

    if args.cmd == "check-config":
        from .config import settings

        problems = settings.problems()
        print(f"env={settings.env}")
        for prob in problems:
            print("  PROBLEM", prob)
        print("OK" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    if args.cmd == "validate":
        from .validate import validate_all

        return validate_all()

    from .db import get_client

    client = get_client()
    if not client.ping():
        print("Neo4j is not reachable — start it with `docker compose up -d neo4j`", file=sys.stderr)
        return 1

    if args.cmd == "schema":
        from .schema import create_schema

        create_schema(client)
    elif args.cmd == "seed":
        from .etl.seed import load_seed

        load_seed(client, embed=not args.no_embed)
    elif args.cmd == "etl":
        from .etl import run

        run(client, args.steps)
    elif args.cmd == "stats":
        from .queries import stats

        print(json.dumps(stats(client), indent=2))
    elif args.cmd == "profile":
        from .queries import drug_profile

        print(json.dumps(drug_profile(client, args.drug), indent=2, default=str))
    elif args.cmd == "eval":
        from .agent.agent import MedGraphAgent
        from .db import get_read_client
        from .evals import run_evals

        return run_evals(MedGraphAgent(get_read_client()))
    elif args.cmd in ("ask", "chat"):
        from .agent.agent import MedGraphAgent
        from .db import get_read_client

        agent = MedGraphAgent(get_read_client())  # the assistant never gets write access
        if args.cmd == "ask":
            _print_answer(agent.ask(args.question))
        else:
            history: list[dict] = []
            while True:
                try:
                    question = input("\nyou> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if question in ("", "exit", "quit"):
                    break
                ans = agent.ask(question, history)
                _print_answer(ans)
                history += [{"role": "user", "content": question}, {"role": "assistant", "content": ans.answer}]
    return 0


def _apikey(name: str, scopes: str) -> int:
    import re

    from .security.auth import SCOPES, generate_key, hash_key

    scope_list = [x.strip() for x in scopes.split(",") if x.strip()]
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name) or not scope_list or set(scope_list) - set(SCOPES):
        print(f"name must be [A-Za-z0-9_.-]{{1,40}} and scopes a subset of {', '.join(SCOPES)}", file=sys.stderr)
        return 2
    key = generate_key()
    print("API key (shown once — give it to the client, do not store it):")
    print(f"  {key}")
    print("Add this entry to MEDGRAPH_API_KEYS (comma-separate multiple entries):")
    print(f"  {name}:{'|'.join(scope_list)}:{hash_key(key)}")
    return 0


def _print_answer(ans) -> None:
    for c in ans.tool_calls:
        print(f"  [tool] {c['tool']}({json.dumps(c['args'])})")
    print(f"\nmedgraph> {ans.answer}")


if __name__ == "__main__":
    raise SystemExit(main())
