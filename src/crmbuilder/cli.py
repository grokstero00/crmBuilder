"""Command-line entry point: ``crmbuilder import`` / ``crmbuilder validate``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from pydantic import ValidationError

from crmbuilder import __version__
from crmbuilder.bitrix import BitrixClient, BitrixError
from crmbuilder.importer import import_portal
from crmbuilder.spec import dump_spec, load_spec
from crmbuilder.state import dump_state


def _cmd_import(args: argparse.Namespace) -> int:
    webhook = args.webhook or os.environ.get("BITRIX_WEBHOOK_URL")
    if not webhook:
        print("error: pass --webhook or set BITRIX_WEBHOOK_URL", file=sys.stderr)
        return 2

    spec_path = Path(args.output)
    state_path = Path(args.state) if args.state else spec_path.with_suffix(".state.json")

    try:
        with BitrixClient(webhook) as client:
            result = import_portal(client, lang=args.lang)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BitrixError as exc:
        print(f"error: Bitrix24 API: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"error: cannot reach the portal: {exc}", file=sys.stderr)
        return 1

    try:
        for path in (spec_path, state_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        dump_spec(result.spec, spec_path)
        dump_state(result.state, state_path)
    except OSError as exc:
        print(f"error: cannot write output: {exc}", file=sys.stderr)
        return 1

    crm = result.spec.crm
    print(f"portal:          {result.spec.portal}")
    print(f"deal categories: {len(crm.deal_categories)}")
    print(f"custom fields:   {sum(len(f) for f in crm.fields.values())}")
    print(f"smart processes: {len(crm.smart_processes)}")
    print(f"departments:     {len(result.spec.structure.departments)}")
    print(f"spec  -> {spec_path}")
    print(f"state -> {state_path}")
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        spec = load_spec(args.spec)
    except FileNotFoundError:
        print(f"error: {args.spec} not found", file=sys.stderr)
        return 2
    except ValidationError as exc:
        print(f"{args.spec}: invalid", file=sys.stderr)
        for err in exc.errors():
            location = ".".join(str(p) for p in err["loc"])
            print(f"  {location}: {err['msg']}", file=sys.stderr)
        return 1
    print(f"{args.spec}: ok ({spec.portal})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crmbuilder", description="Bitrix24 portal configuration as code."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="read a live portal into a spec + state file")
    p_import.add_argument("--webhook", help="incoming webhook URL (default: $BITRIX_WEBHOOK_URL)")
    p_import.add_argument("-o", "--output", default="portal.yaml", help="spec file to write")
    p_import.add_argument("--state", help="state file to write (default: <output>.state.json)")
    p_import.add_argument(
        "--lang", default=os.environ.get("BITRIX_LANG", "ru"), help="language of field labels"
    )
    p_import.set_defaults(func=_cmd_import)

    p_validate = sub.add_parser("validate", help="check a spec file against the schema")
    p_validate.add_argument("spec", help="path to spec YAML")
    p_validate.set_defaults(func=_cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
