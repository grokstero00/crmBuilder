"""Command-line entry point: ``crmbuilder import | plan | validate``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
from pydantic import ValidationError

from crmbuilder import __version__
from crmbuilder.bitrix import BitrixClient, BitrixError
from crmbuilder.importer import ImportResult, import_portal
from crmbuilder.plan import diff_specs
from crmbuilder.spec import PortalSpec, dump_spec, load_spec
from crmbuilder.state import State, dump_state, load_state


class _Failure(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def _state_path(spec_path: Path, explicit: str | None) -> Path:
    return Path(explicit) if explicit else spec_path.with_suffix(".state.json")


def _open_client(args: argparse.Namespace) -> BitrixClient:
    webhook = args.webhook or os.environ.get("BITRIX_WEBHOOK_URL")
    if not webhook:
        raise _Failure("pass --webhook or set BITRIX_WEBHOOK_URL", 2)
    try:
        return BitrixClient(webhook)
    except ValueError as exc:
        raise _Failure(str(exc), 2) from exc


def _read_portal(client: BitrixClient, lang: str, previous: State | None) -> ImportResult:
    try:
        return import_portal(client, lang=lang, previous_state=previous)
    except BitrixError as exc:
        raise _Failure(f"Bitrix24 API: {exc}") from exc
    except httpx.HTTPError as exc:
        raise _Failure(f"cannot reach the portal: {exc}") from exc
    finally:
        client.close()


def _load_spec(path: str) -> PortalSpec:
    try:
        return load_spec(path)
    except FileNotFoundError as exc:
        raise _Failure(f"{path} not found", 2) from exc
    except ValidationError as exc:
        details = "\n".join(
            f"  {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise _Failure(f"{path}: invalid\n{details}") from exc


def _check_portal(expected: str, client: BitrixClient, what: str) -> None:
    if expected != client.portal:
        raise _Failure(
            f"{what} belongs to {expected}, but the webhook points to {client.portal}", 2
        )


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _cmd_import(args: argparse.Namespace) -> int:
    spec_path = Path(args.output)
    state_path = _state_path(spec_path, args.state)
    client = _open_client(args)

    previous = None
    if state_path.exists() and not args.fresh:
        previous = load_state(state_path)
        _check_portal(previous.portal, client, str(state_path))

    result = _read_portal(client, args.lang, previous)

    try:
        for path in (spec_path, state_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        dump_spec(result.spec, spec_path)
        dump_state(result.state, state_path)
    except OSError as exc:
        raise _Failure(f"cannot write output: {exc}") from exc

    crm = result.spec.crm
    print(f"portal:          {result.spec.portal}")
    print(f"deal categories: {len(crm.deal_categories)}")
    print(f"custom fields:   {sum(len(f) for f in crm.fields.values())}")
    print(f"smart processes: {len(crm.smart_processes)}")
    print(f"departments:     {len(result.spec.structure.departments)}")
    print(f"spec  -> {spec_path}")
    print(f"state -> {state_path}" + (" (keys kept from previous import)" if previous else ""))
    _print_warnings(result.warnings)
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    desired = _load_spec(args.spec)

    if args.against:
        current = _load_spec(args.against)
        warnings: list[str] = []
    else:
        state_path = _state_path(Path(args.spec), args.state)
        if not state_path.exists():
            raise _Failure(
                f"{state_path} not found: run `crmbuilder import` first, "
                "the state file links spec keys to the portal",
                2,
            )
        client = _open_client(args)
        _check_portal(desired.portal, client, args.spec)
        result = _read_portal(client, args.lang, load_state(state_path))
        current, warnings = result.spec, result.warnings

    plan = diff_specs(current, desired)
    print(plan.to_json() if args.json else plan.render())
    _print_warnings(warnings)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    spec = _load_spec(args.spec)
    print(f"{args.spec}: ok ({spec.portal})")
    return 0


def _add_portal_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--webhook", help="incoming webhook URL (default: $BITRIX_WEBHOOK_URL)")
    parser.add_argument(
        "--lang", default=os.environ.get("BITRIX_LANG", "ru"), help="language of field labels"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crmbuilder", description="Bitrix24 portal configuration as code."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="read a live portal into a spec + state file")
    _add_portal_options(p_import)
    p_import.add_argument("-o", "--output", default="portal.yaml", help="spec file to write")
    p_import.add_argument("--state", help="state file (default: <output>.state.json)")
    p_import.add_argument(
        "--fresh",
        action="store_true",
        help="ignore an existing state file and generate all keys anew",
    )
    p_import.set_defaults(func=_cmd_import)

    p_plan = sub.add_parser(
        "plan", help="show what would change on the portal to match the spec (read-only)"
    )
    p_plan.add_argument("spec", help="desired spec YAML")
    _add_portal_options(p_plan)
    p_plan.add_argument("--state", help="state file (default: <spec>.state.json)")
    p_plan.add_argument(
        "--against", metavar="SPEC", help="compare with another spec file instead of the portal"
    )
    p_plan.add_argument("--json", action="store_true", help="print the plan as JSON")
    p_plan.set_defaults(func=_cmd_plan)

    p_validate = sub.add_parser("validate", help="check a spec file against the schema")
    p_validate.add_argument("spec", help="path to spec YAML")
    p_validate.set_defaults(func=_cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except _Failure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
