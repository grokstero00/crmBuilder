"""Command-line entry point: ``crmbuilder import | plan | apply | rollback | design | ...``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
import yaml
from pydantic import ValidationError

from crmbuilder import __version__, automation, templates
from crmbuilder.apply import Applier
from crmbuilder.bitrix import BitrixClient, BitrixError
from crmbuilder.importer import ImportResult, import_portal
from crmbuilder.plan import Plan, diff_specs
from crmbuilder.spec import PortalSpec, dump_spec, load_spec
from crmbuilder.state import State, dump_state, load_state


class _Failure(Exception):
    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def _state_path(spec_path: Path, explicit: str | None) -> Path:
    return Path(explicit) if explicit else spec_path.with_suffix(".state.json")


def _open_client(args: argparse.Namespace, *, writable: bool = False) -> BitrixClient:
    webhook = args.webhook or os.environ.get("BITRIX_WEBHOOK_URL")
    if not webhook:
        raise _Failure("pass --webhook or set BITRIX_WEBHOOK_URL", 2)
    try:
        return BitrixClient(webhook, read_only=not writable)
    except ValueError as exc:
        raise _Failure(str(exc), 2) from exc


def _read_portal(client: BitrixClient, lang: str, previous: State | None) -> ImportResult:
    try:
        return import_portal(client, lang=lang, previous_state=previous)
    except BitrixError as exc:
        raise _Failure(f"Bitrix24 API: {exc}") from exc
    except httpx.HTTPError as exc:
        message = f"cannot reach the portal: {exc}"
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            message += (
                "\nhint: the portal's certificate is not trusted inside the container. This is "
                "usually an antivirus or corporate proxy that inspects HTTPS, or a national "
                "certificate authority. Save that root certificate as workspace/ca.pem and set "
                "BITRIX_CA_BUNDLE=/workspace/ca.pem in .env (see README, 'Ошибка SSL')."
            )
        raise _Failure(message) from exc
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
    # Robots cannot be read from the portal: keep the rules from the spec being replaced.
    old_spec = _previous_spec(spec_path)
    spec, dropped = automation.carry_over(result.spec, old_spec)
    result.spec = spec
    result.warnings += dropped

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
    if result.spec.automation:
        print(f"automation:      {len(result.spec.automation)} rule(s) kept from {spec_path}")
    print(f"spec  -> {spec_path}")
    print(f"state -> {state_path}" + (" (keys kept from previous import)" if previous else ""))
    _print_warnings(result.warnings)
    return 0


def _previous_spec(path: Path) -> PortalSpec | None:
    if not path.exists():
        return None
    try:
        return load_spec(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"warning: {path} is not a valid spec, its automation rules are not kept: {exc}",
              file=sys.stderr)  # fmt: skip
        return None


def _print_manual(spec: PortalSpec) -> None:
    text = automation.render_pending(spec)
    if text:
        print()
        print(text)


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
    if not args.json:
        _print_manual(desired)
    _print_warnings(warnings)
    return 0


_WRITE_ENV = "BITRIX_ALLOW_WRITE"


def _check_writes_allowed() -> None:
    if os.environ.get(_WRITE_ENV, "").strip().lower() not in ("1", "y", "yes", "true"):
        raise _Failure(
            f"writing to this portal is not allowed: set {_WRITE_ENV}=yes in .env for portals "
            "you may change (keep it unset for production portals). Nothing was changed.",
            2,
        )


def _new_snapshot_dir(spec_path: Path) -> Path:
    base = spec_path.parent / ".history"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path, n = base / stamp, 2
    while path.exists():
        path, n = base / f"{stamp}-{n}", n + 1
    path.mkdir(parents=True)
    return path


def _confirm(portal: str, count: int) -> bool:
    print(f"\nApply {count} change(s) to {portal}? Type the portal name to confirm: ", end="")
    try:
        answer = input()
    except EOFError:
        return False
    return answer.strip() == portal


def _apply_spec(args: argparse.Namespace, desired: PortalSpec, spec_path: Path) -> bool:
    """Bring the portal to ``desired``. Returns True when it was applied and verified."""
    _check_writes_allowed()
    state_path = _state_path(spec_path, args.state)
    if not state_path.exists():
        raise _Failure(f"{state_path} not found: run `crmbuilder import` first", 2)

    reader = _open_client(args)
    _check_portal(desired.portal, reader, str(spec_path))
    before = _read_portal(reader, args.lang, load_state(state_path))
    plan = diff_specs(before.spec, desired)
    print(plan.render())
    _print_warnings(before.warnings)
    if not plan.changes:
        return True

    writer = _open_client(args, writable=True)
    applier = Applier(writer, before.spec, desired, before.state)
    problems = applier.unsupported(plan)
    if problems:
        lines = "\n".join(f"  {p.change.address}: {p.reason}" for p in problems)
        raise _Failure(f"these changes cannot be applied:\n{lines}\nNothing was changed.", 2)
    if plan.destructive and not args.allow_destructive:
        raise _Failure(
            f"the plan has {len(plan.destructive)} destructive change(s); review them and "
            "re-run with --allow-destructive. Nothing was changed.",
            2,
        )
    if not args.yes and not _confirm(desired.portal, len(plan.changes)):
        raise _Failure("aborted, nothing was changed")

    snapshot = _new_snapshot_dir(spec_path)
    dump_spec(before.spec, snapshot / "before.yaml")
    dump_state(before.state, snapshot / "before.state.json")
    dump_spec(desired, snapshot / "desired.yaml")
    (snapshot / "plan.json").write_text(plan.to_json() + "\n", encoding="utf-8")
    journal_file = (snapshot / "journal.jsonl").open("a", encoding="utf-8")

    def journal(entry: dict) -> None:
        entry = {"time": datetime.now().isoformat(timespec="seconds"), **entry}
        journal_file.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        journal_file.flush()

    applier.journal = journal
    try:
        result = applier.apply(plan)
    finally:
        journal_file.close()
        writer.close()
        # Whatever happened, the state file must describe what is really on the portal.
        dump_state(applier.state, state_path)

    print()
    print(f"applied {len(result.done)} of {len(plan.changes)} change(s)")
    _print_warnings(result.warnings)
    if not result.ok:
        print(f"error: {result.failed.address}: {result.error}", file=sys.stderr)
        print("the remaining changes were not applied; run `plan` to see them", file=sys.stderr)
    print(f"snapshot and journal: {snapshot}")

    after = _read_portal(_open_client(args), args.lang, applier.state)
    dump_state(after.state, state_path)
    residual = diff_specs(after.spec, desired)
    if result.ok and not residual.changes:
        print("verified: the portal matches the spec")
        return True
    if residual.changes:
        print("\nthe portal still differs from the spec:")
        print(_indent(residual))
    return False


def _indent(plan: Plan) -> str:
    return "\n".join("  " + line for line in plan.render().splitlines())


def _cmd_apply(args: argparse.Namespace) -> int:
    desired = _load_spec(args.spec)
    ok = _apply_spec(args, desired, Path(args.spec))
    _print_manual(desired)
    return 0 if ok else 1


def _cmd_rollback(args: argparse.Namespace) -> int:
    spec_path = Path(args.spec)
    history = spec_path.parent / ".history"
    snapshots = sorted(p for p in history.glob("*") if (p / "before.yaml").exists())
    if args.to:
        snapshots = [p for p in snapshots if p.name == args.to]
    if not snapshots:
        what = f"snapshot {args.to}" if args.to else "snapshots"
        raise _Failure(f"no {what} found in {history}", 2)
    snapshot = snapshots[-1]
    print(f"rolling back to the portal as it was before {snapshot.name}\n")
    desired = _load_spec(str(snapshot / "before.yaml"))
    ok = _apply_spec(args, desired, spec_path)
    if ok:
        restored, dropped = automation.carry_over(desired, _previous_spec(spec_path))
        dump_spec(restored, spec_path)
        print(f"{spec_path} now matches the restored portal")
        _print_warnings(dropped)
    return 0 if ok else 1


def _anthropic_client():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - installed as a dependency
        raise _Failure("the anthropic package is not installed", 2) from exc
    from crmbuilder.bitrix.client import ssl_context

    try:
        verify = ssl_context()
    except ValueError as exc:
        raise _Failure(str(exc), 2) from exc
    # The same extra CA as for Bitrix: HTTPS inspection usually covers both.
    return anthropic.Anthropic(http_client=anthropic.DefaultHttpxClient(verify=verify))


def _ask_in_console(questions: list[str]) -> list[str]:
    print("\nClaude asks (press Enter to let it decide):")
    answers = []
    for n, question in enumerate(questions, 1):
        print(f"\n{n}. {question}")
        try:
            answers.append(input("> ").strip())
        except EOFError:
            answers.append("")
    print()
    return answers


def _cmd_design(args: argparse.Namespace) -> int:
    import anthropic

    from crmbuilder import design as designer

    spec = _load_spec(args.spec)
    if args.brief:
        try:
            brief = Path(args.brief).read_text(encoding="utf-8")
        except OSError as exc:
            raise _Failure(f"cannot read the brief: {exc}", 2) from exc
    else:
        brief = args.brief_text or ""
    if not brief.strip():
        raise _Failure("give the task with --brief FILE or --brief-text TEXT", 2)

    model = args.model or os.environ.get("CRMBUILDER_MODEL", designer.DEFAULT_MODEL)
    effort = args.effort or os.environ.get("CRMBUILDER_EFFORT", designer.DEFAULT_EFFORT)
    print(f"designing with {model} (effort {effort}); only the portal structure is sent\n")
    try:
        result = designer.design(
            _anthropic_client(),
            spec,
            brief,
            ask=None if args.no_questions else _ask_in_console,
            report=print,
            model=model,
            effort=effort,
        )
    except designer.DesignError as exc:
        raise _Failure(str(exc)) from exc
    except anthropic.AuthenticationError as exc:
        raise _Failure(
            "the Anthropic API key is missing or invalid: set ANTHROPIC_API_KEY in .env "
            "(console.anthropic.com -> API keys)",
            2,
        ) from exc
    except anthropic.PermissionDeniedError as exc:
        raise _Failure(f"Anthropic API refused access: {exc.message}") from exc
    except anthropic.RateLimitError as exc:
        raise _Failure("Anthropic API rate limit reached, try again in a minute") from exc
    except anthropic.APIStatusError as exc:
        raise _Failure(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        hint = ""
        if "CERTIFICATE_VERIFY_FAILED" in repr(exc.__cause__) or "CERTIFICATE" in str(exc):
            hint = " (certificate problem: see BITRIX_CA_BUNDLE in README, it applies here too)"
        raise _Failure(f"cannot reach the Anthropic API{hint}: {exc}") from exc

    print("\n" + (result.summary or "(no summary)"))
    usage = result.usage
    cost = usage.cost(model)
    if cost is None:
        cost_text = ", cost unknown for this model"
    elif cost < 0.01:
        cost_text = f", about ${cost:.4f}"
    else:
        cost_text = f", about ${cost:.2f}"
    read = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    print(
        f"\n[{usage.requests} request(s): {read} tokens read, "
        f"{usage.output_tokens} written{cost_text}]"
    )

    plan = diff_specs(spec, result.spec)
    print()
    print(_indent(plan))
    if not plan.changes:
        return 0

    target = Path(args.output) if args.output else Path(args.spec)
    if not args.yes:
        print(f"\nSave these changes to {target}? The portal is not touched. [y/N] ", end="")
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes", "д", "да"):
            print("not saved")
            return 1
    result_spec = result.spec.model_copy(update={"portal": spec.portal})
    dump_spec(result_spec, target)
    print(f"saved to {target}; next: crmbuilder plan {target}, then apply")
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    from crmbuilder import audit as auditor

    spec = _load_spec(args.spec)
    findings = auditor.audit(spec)
    business = ""
    cost_line = ""
    if args.ai:
        import anthropic

        from crmbuilder import design as designer

        model = args.model or os.environ.get("CRMBUILDER_MODEL", designer.DEFAULT_MODEL)
        effort = args.effort or os.environ.get("CRMBUILDER_EFFORT", designer.DEFAULT_EFFORT)
        context = args.context or ""
        if args.context_file:
            try:
                context = Path(args.context_file).read_text(encoding="utf-8")
            except OSError as exc:
                raise _Failure(f"cannot read the context file: {exc}", 2) from exc
        if not args.json:
            print(f"AI review with {model}; only the portal structure is sent\n")
        try:
            review = auditor.ai_review(
                _anthropic_client(), spec, findings, context=context, model=model, effort=effort
            )
        except anthropic.AuthenticationError as exc:
            raise _Failure("set ANTHROPIC_API_KEY in .env to use --ai", 2) from exc
        except (anthropic.APIError, RuntimeError) as exc:
            raise _Failure(f"AI review failed: {exc}") from exc
        findings = findings + review.findings
        business = review.business_guess
        cost = review.usage.cost(model)
        if cost is not None:
            cost_line = (
                f"[AI review: about ${cost:.4f}]"
                if cost < 0.01
                else f"[AI review: about ${cost:.2f}]"
            )

    if args.json:
        print(json.dumps({"portal": spec.portal, "business_guess": business,
                          "findings": [f.to_dict() for f in findings]},
                         ensure_ascii=False, indent=2))  # fmt: skip
    else:
        print(auditor.render_text(spec.portal, findings, business))
        if cost_line:
            print(cost_line)
    if args.output:
        try:
            Path(args.output).write_text(
                auditor.render_markdown(spec.portal, findings, business), encoding="utf-8"
            )
        except OSError as exc:
            raise _Failure(f"cannot write the report: {exc}") from exc
        if not args.json:
            print(f"report saved to {args.output}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    from crmbuilder import report as reporter

    spec_path = Path(args.spec)
    spec = _load_spec(args.spec)

    before_path: Path | None = None
    if args.before:
        before_path = Path(args.before)
    else:
        history = spec_path.parent / ".history"
        snapshots = sorted(p for p in history.glob("*") if (p / "before.yaml").exists())
        if args.since:
            snapshots = [p for p in snapshots if p.name == args.since]
            if not snapshots:
                raise _Failure(f"snapshot {args.since} not found in {history}", 2)
        if snapshots:
            before_path = snapshots[0] / "before.yaml"
    changes = None
    if before_path is not None:
        changes = reporter.describe_changes(_load_spec(str(before_path)), spec)
        print(f"changes counted from {before_path}")
    else:
        print("no snapshot found: the report will describe the structure only")

    ai_part = None
    if args.ai:
        import anthropic

        from crmbuilder import design as designer

        if not args.brief:
            raise _Failure("--ai needs --brief: manual steps are derived from the brief", 2)
        try:
            brief = Path(args.brief).read_text(encoding="utf-8")
        except OSError as exc:
            raise _Failure(f"cannot read the brief: {exc}", 2) from exc
        model = args.model or os.environ.get("CRMBUILDER_MODEL", designer.DEFAULT_MODEL)
        effort = args.effort or os.environ.get("CRMBUILDER_EFFORT", designer.DEFAULT_EFFORT)
        print(f"writing manual steps with {model}; only the portal structure is sent")
        try:
            ai_part = reporter.ai_manual_steps(
                _anthropic_client(), spec, changes or [], brief, model=model, effort=effort
            )
        except anthropic.AuthenticationError as exc:
            raise _Failure("set ANTHROPIC_API_KEY in .env to use --ai", 2) from exc
        except (anthropic.APIError, RuntimeError) as exc:
            raise _Failure(f"AI part failed: {exc}") from exc
        cost = ai_part.usage.cost(model)
        if cost is not None:
            print(f"[AI: about ${cost:.4f}]" if cost < 0.01 else f"[AI: about ${cost:.2f}]")

    document = reporter.render(spec, changes=changes, title=args.title or "", ai=ai_part)
    output = Path(args.output) if args.output else spec_path.with_name("report.md")
    try:
        output.write_text(document, encoding="utf-8")
    except OSError as exc:
        raise _Failure(f"cannot write the report: {exc}") from exc
    done = len(changes) if changes is not None else 0
    manual = len(ai_part.manual_steps) if ai_part else 0
    print(f"report saved to {output}: {done} change(s), {manual} manual step(s)")
    return 0


def _cmd_template_list(args: argparse.Namespace) -> int:
    for name, description in templates.available().items():
        print(f"{name:<16}{description}")
    return 0


def _cmd_template_add(args: argparse.Namespace) -> int:
    spec = _load_spec(args.spec)
    try:
        merged, report = templates.merge(spec, args.name)
    except ValueError as exc:
        raise _Failure(str(exc), 2) from exc
    except ValidationError as exc:
        raise _Failure(f"template {args.name!r} does not fit this spec: {exc}") from exc
    dump_spec(merged, args.spec)
    print(f"template {args.name!r} merged into {args.spec}: {len(report.added)} added")
    for address in report.added:
        print(f"  + {address}")
    for address in report.skipped:
        print(f"  = {address} (already in the spec, left as is)")
    print(f"\nnothing has changed on the portal yet; next: crmbuilder plan {args.spec}")
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

    def add_write_options(parser: argparse.ArgumentParser) -> None:
        _add_portal_options(parser)
        parser.add_argument("--state", help="state file (default: <spec>.state.json)")
        parser.add_argument(
            "--allow-destructive",
            action="store_true",
            help="allow deletes and field re-creation, which can lose data",
        )
        parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")

    p_apply = sub.add_parser(
        "apply", help=f"change the portal to match the spec (needs {_WRITE_ENV}=yes)"
    )
    p_apply.add_argument("spec", help="desired spec YAML")
    add_write_options(p_apply)
    p_apply.set_defaults(func=_cmd_apply)

    p_rollback = sub.add_parser(
        "rollback", help="restore the portal as it was before the last apply"
    )
    p_rollback.add_argument("spec", help="spec YAML that was applied")
    p_rollback.add_argument("--to", metavar="SNAPSHOT", help="snapshot name from .history")
    add_write_options(p_rollback)
    p_rollback.set_defaults(func=_cmd_rollback)

    p_design = sub.add_parser(
        "design", help="let Claude edit the spec from a brief (ТЗ); the portal is not touched"
    )
    p_design.add_argument("spec", help="spec YAML to edit")
    p_design.add_argument("--brief", metavar="FILE", help="text file with the brief")
    p_design.add_argument("--brief-text", metavar="TEXT", help="the brief as a string")
    p_design.add_argument("-o", "--output", help="write the result here (default: the spec)")
    p_design.add_argument("--model", help="Claude model (default: $CRMBUILDER_MODEL or Opus 5.5)")
    p_design.add_argument(
        "--effort",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="how hard the model thinks (default: $CRMBUILDER_EFFORT or high)",
    )
    p_design.add_argument(
        "--no-questions", action="store_true", help="do not ask, decide on assumptions"
    )
    p_design.add_argument("--yes", action="store_true", help="save without asking")
    p_design.set_defaults(func=_cmd_design)

    p_audit = sub.add_parser("audit", help="find problems in a spec (read-only)")
    p_audit.add_argument("spec", help="spec YAML, e.g. right after import")
    p_audit.add_argument("--ai", action="store_true", help="add a review by Claude")
    p_audit.add_argument("--context", help="a few words about the client's business, for --ai")
    p_audit.add_argument("--context-file", help="file describing the client's business")
    p_audit.add_argument("--model", help="Claude model for --ai")
    p_audit.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p_audit.add_argument("--json", action="store_true", help="print findings as JSON")
    p_audit.add_argument("-o", "--output", help="also save a Markdown report for the client")
    p_audit.set_defaults(func=_cmd_audit)

    p_report = sub.add_parser("report", help="hand-over report for the client (read-only)")
    p_report.add_argument("spec", help="spec YAML of the finished portal")
    p_report.add_argument(
        "--since",
        metavar="SNAPSHOT",
        help="count changes from this .history snapshot "
        "(default: the earliest one, i.e. the portal before the project)",
    )
    p_report.add_argument("--before", metavar="SPEC", help="count changes from this spec file")
    p_report.add_argument("--brief", help="the brief (ТЗ), needed for --ai")
    p_report.add_argument("--ai", action="store_true", help="add manual steps written by Claude")
    p_report.add_argument("--title", help="document title")
    p_report.add_argument("--model", help="Claude model for --ai")
    p_report.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p_report.add_argument(
        "-o", "--output", help="where to save (default: report.md next to the spec)"
    )
    p_report.set_defaults(func=_cmd_report)

    p_template = sub.add_parser("template", help="ready-made configurations")
    template_sub = p_template.add_subparsers(dest="template_command", required=True)
    p_tlist = template_sub.add_parser("list", help="show available templates")
    p_tlist.set_defaults(func=_cmd_template_list)
    p_tadd = template_sub.add_parser(
        "add", help="merge a template into a spec file (the portal is not touched)"
    )
    p_tadd.add_argument("name", help="template name, see `template list`")
    p_tadd.add_argument("spec", help="spec YAML to extend")
    p_tadd.set_defaults(func=_cmd_template_add)

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
