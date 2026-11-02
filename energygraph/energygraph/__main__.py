"""EnergyGraph command line.

    python -m energygraph seed                       # synthetic sample world + synthetic organization samples
    python -m energygraph etl all                    # public sources (+ organization sources if configured)
    python -m energygraph etl schema oeo gppd wikidata gleif eia similarity embed
    python -m energygraph sources                    # catalog: licence, availability
    python -m energygraph org validate <file|dir>    # contract-check organization payloads
    python -m energygraph org status | org ingest [dataset ...]
    python -m energygraph validate | check-config | stats
    python -m energygraph profile "Harbor Point LNG" [--entitlements public,org:outages]
    python -m energygraph ask "Who owns Harbor Point LNG?" [--entitlements ...]
    python -m energygraph chat | eval | serve
    python -m energygraph apikey analyst --scopes read,chat --entitlements public,org:power-assets
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SAMPLES = Path(__file__).resolve().parent / "sources" / "organization" / "samples"


def _principal(ents: str | None):
    from .security.auth import Principal

    values = [e.strip() for e in (ents or "public").split(",") if e.strip()]
    return Principal("cli", frozenset({"read", "chat"}), frozenset(values) | {"public"}, kind="cli")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="energygraph")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("schema")
    s = sub.add_parser("seed", help="load synthetic sample world + synthetic organization samples")
    s.add_argument("--no-embed", action="store_true")
    e = sub.add_parser("etl", help="run ETL steps")
    e.add_argument("steps", nargs="+")
    e.add_argument("--limit", type=int, default=None)
    sub.add_parser("sources", help="data source catalog and availability")
    o = sub.add_parser("org", help="organization-source tools")
    o.add_argument("action", choices=["validate", "status", "ingest"])
    o.add_argument("targets", nargs="*")
    sub.add_parser("validate")
    sub.add_parser("check-config")
    sub.add_parser("stats")
    pr = sub.add_parser("profile")
    pr.add_argument("asset")
    pr.add_argument("--entitlements", default="public")
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--entitlements", default="public")
    c = sub.add_parser("chat")
    c.add_argument("--entitlements", default="public")
    sub.add_parser("eval")
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8100)
    sv.add_argument("--workers", type=int, default=1)
    k = sub.add_parser("apikey")
    k.add_argument("name")
    k.add_argument("--scopes", default="read,chat")
    k.add_argument("--entitlements", default="public")
    args = p.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("energygraph.api.main:app", host=args.host, port=args.port, workers=args.workers,
                    proxy_headers=False, server_header=False, timeout_keep_alive=5)
        return 0
    if args.cmd == "apikey":
        return _apikey(args.name, args.scopes, args.entitlements)
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
    if args.cmd == "org" and args.action == "validate":
        return _org_validate(args.targets)
    if args.cmd == "sources":
        from .queries import list_sources

        for s in list_sources(None, _principal("*"))["sources"]:
            print(f"  {s['source']:22s} {s['kind']:12s} {'ok' if s['available'] else s['status']:45s} {s['license']}")
        return 0

    from .db import get_client

    client = get_client()
    if not client.ping():
        print("Neo4j is not reachable — start it with `docker compose up -d neo4j`", file=sys.stderr)
        return 1

    if args.cmd == "schema":
        from .etl.pipeline import run

        run(client, ["schema"])
    elif args.cmd == "seed":
        from .etl.pipeline import run, run_source
        from .sources.organization.connector import DATASETS, FileTransport, OrganizationSource
        from .sources.base import registry

        run(client, ["schema", "energygraph-core", "seed"])
        for dataset in DATASETS:
            if (SAMPLES / f"{dataset}.jsonl").exists():
                registry()[f"org-{dataset}"] = OrganizationSource(dataset, FileTransport(SAMPLES))
                print(run_source(client, f"org-{dataset}"))
        run(client, ["similarity"] + ([] if args.no_embed else ["embed"]))
        print("seed: synthetic sample world loaded (illustrative only)")
    elif args.cmd == "etl":
        from .etl.pipeline import run

        run(client, args.steps, limit=args.limit)
    elif args.cmd == "org":
        from .etl.pipeline import run_source
        from .sources.base import registry

        org = {k: v for k, v in registry().items() if k.startswith("org-")}
        targets = [f"org-{t}" for t in args.targets] or list(org)
        for sid in targets:
            if sid not in org:
                print(f"  unknown dataset {sid[4:]!r}")
                continue
            if args.action == "status":
                ok, reason = org[sid].available()
                print(f"  {sid:22s} {'ready' if ok else 'not ready'}: {reason}")
            else:
                print(run_source(client, sid))
    elif args.cmd == "stats":
        from .queries import stats

        print(json.dumps(stats(client), indent=2))
    elif args.cmd == "profile":
        from .queries import asset_overview

        print(json.dumps(asset_overview(client, args.asset, _principal(args.entitlements).ents()), indent=2,
                         default=str))
    elif args.cmd in ("ask", "chat", "eval"):
        from .agent.agent import EnergyGraphAgent
        from .db import get_read_client

        agent = EnergyGraphAgent(get_read_client())  # the assistant never gets write access
        if args.cmd == "eval":
            from .evals import run_evals

            return run_evals(agent)
        principal = _principal(args.entitlements)
        if args.cmd == "ask":
            _print(agent.ask(args.question, principal=principal))
        else:
            history: list[dict] = []
            while True:
                try:
                    question = input("\nyou> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if question in ("", "exit", "quit"):
                    break
                ans = agent.ask(question, history, principal=principal)
                _print(ans)
                history += [{"role": "user", "content": question}, {"role": "assistant", "content": ans.answer}]
    return 0


def _org_validate(targets: list[str]) -> int:
    from .sources.organization.connector import validate_file

    paths: list[Path] = []
    for t in targets or [str(SAMPLES)]:
        path = Path(t)
        paths += sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    bad = 0
    for path in paths:
        r = validate_file(path)
        if "error" in r:
            print(f"  {r['file']:22s} ERROR {r['error']}")
            bad += 1
            continue
        print(f"  {r['file']:22s} valid={r['valid']:<6d} invalid={r['invalid']}")
        for err in r["errors"][:5]:
            print(f"      - {err['record_id'] or '?'}: {err['errors'][0]['loc']} {err['errors'][0]['msg']}")
        bad += bool(r["invalid"])
    return 1 if bad else 0


def _apikey(name: str, scopes: str, entitlements: str) -> int:
    from .security.auth import ENTITLEMENT_RE, SCOPES, generate_key, hash_key

    scope_list = [x.strip() for x in scopes.split(",") if x.strip()]
    ent_list = [x.strip() for x in entitlements.split(",") if x.strip()]
    if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name) or not scope_list or set(scope_list) - set(SCOPES)
            or not ent_list or not all(ENTITLEMENT_RE.match(e) for e in ent_list)):
        print("invalid name, scopes (read,chat,admin) or entitlements (public, org:<dataset>, *)", file=sys.stderr)
        return 2
    key = generate_key()
    print("API key (shown once — give it to the client, do not store it):")
    print(f"  {key}")
    print("Add this entry to ENERGYGRAPH_API_KEYS (comma-separate multiple entries):")
    print(f"  {name}:{'|'.join(scope_list)}:{'|'.join(ent_list)}:{hash_key(key)}")
    return 0


def _print(ans) -> None:
    for c in ans.tool_calls:
        print(f"  [tool] {c['tool']}({json.dumps(c['args'])}) -> {c['outcome']}")
    print(f"\nenergygraph> {ans.answer}")


if __name__ == "__main__":
    raise SystemExit(main())
