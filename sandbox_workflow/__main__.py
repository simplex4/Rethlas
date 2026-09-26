"""Command-line entry point for the sandboxed managed-account workflow."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid

from .core import (PACKAGE, REPO, Workflow, WorkflowError, atomic, codex_home,
                   command, parse_events, read_json, settings, sha)
from .timer import ElapsedTimer


def terminal_progress(event, details):
    turn = details["turn"]
    log = details["log"]
    action = "Starting" if event == "attempt_started" else "Finished"
    if turn["role"] == "generator":
        print(f"{action} iter={turn['iteration']} role=generator -> {log}", flush=True)
    else:
        print(f"{action} verifier attempt={turn['attempt']} -> {log}", flush=True)


def print_run_settings(manifest, run, iterations, launch_mode):
    config = manifest["settings"]
    print("========================================")
    print(f" Codex:      {config['codex_bin']}")
    print(f" Codex home: {config['codex_home']}")
    print(f" Generator:  {config['generator_model']}")
    print(f" Gen effort: {config['generator_effort']}")
    print(f" Verifier:   {config['verifier_model']}")
    print(f" Ver effort: {config['verifier_effort']}")
    print(f" Sandbox:    {config['sandbox']}")
    print(f" Approval:   {config['approval_policy']}")
    print(f" Problem:    {manifest['problem']}")
    print(f" Run ID:     {manifest['run_id']}")
    print(f" Mode:       {launch_mode}")
    print(f" Policy:     {manifest.get('mode_policy', 'fixed')}")
    if manifest.get("generator_session"):
        print(f" Session:    {manifest['generator_session']}")
    print(f" Next iter:  {manifest['next_iteration']}")
    print(f" Max iters:  {iterations}")
    print(f" Logs:       {run / 'logs'}")
    print(f" Stop file:  {run / 'results' / 'blueprint_verified.md'}")
    print(f" Pause file: {run / 'PAUSE_AFTER_TURN'}")
    print("========================================")
    print("")


def print_run_result(result, run, iterations):
    run_id = result["run_id"]
    problem = result["problem"]
    problem_path = Path(problem)
    try:
        problem_id = problem_path.relative_to("agents/generation/data").with_suffix("")
    except ValueError:
        problem_id = problem_path.with_suffix("")
    status = result["status"]
    if status == "accepted":
        verified = run / "results" / "blueprint_verified.md"
        print(f"Solved problem_id={problem_id} -> {verified}")
        print("")
        print("To export results, run:")
        print(f"  python3 -m sandbox_workflow export --run-id {run_id}")
        return
    if status == "paused":
        print(f"Run paused. To continue from the next unfinished turn, run:")
        print(f"  python3 -m sandbox_workflow resume --run-id {run_id} --clear-pause --iterations {iterations}")
        return
    if result.get("mode_policy") == "improvement":
        print("Improvement budget completed; all accepted proofs remain under "
              f"{run / 'candidates'}. This is not a claim of optimality.")
    else:
        print(f"Completed MAX_ITERATIONS={iterations} without verified blueprint for problem_id={problem_id}")
    print("Run the resume command below to continue from the next unused iteration:")
    print(f"  python3 -m sandbox_workflow resume --run-id {run_id} --iterations {iterations}")


def diagnostic(cmd, env):
    try:
        p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, timeout=30)
        return {"ok": p.returncode == 0, "returncode": p.returncode,
                "output": (p.stdout + p.stderr).strip()}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc)}


def doctor(live=False):
    config = settings()
    env = os.environ.copy()
    env["CODEX_HOME"] = config["codex_home"]
    env.pop("CODEX_API_KEY", None)
    env.pop("OPENAI_API_KEY", None)
    result = {"settings": config, "python": sys.version.split()[0],
              "pdftotext": shutil.which("pdftotext"),
              "version": diagnostic([config["codex_bin"], "--version"], env),
              "authentication": diagnostic([config["codex_bin"], "login", "status"], env),
              "live_checked": False}
    if not live:
        result["note"] = "Static checks do not establish sandbox writes, network, or model availability. No dependencies installed."
        return result
    if not result["version"]["ok"] or not result["authentication"]["ok"]:
        raise WorkflowError("Codex version/authentication checks failed: " + json.dumps(result))
    # A retained, isolated artifact directory makes the exact invocation reviewable.
    workspace = REPO / ".local" / "sandbox-workflow" / "probes" / uuid.uuid4().hex
    workspace.mkdir(parents=True)
    shutil.copy2(PACKAGE / "research.py", workspace / "research.py")
    shutil.copy2(PACKAGE / "research_contract.py", workspace / "research_contract.py")
    atomic(workspace / "turn.json", {"run_id": "doctor", "attempt": 0, "search_mode": "live"})
    atomic(workspace / "AGENTS.md", b"This is a bounded runtime capability probe. No delegation. Use no MCP. Do not install anything or request escalation. Stay in this workspace.\n")
    props = {"shell_ok": {"type": "boolean"}, "write_ok": {"type": "boolean"},
             "retrieval_ok": {"type": "boolean"}, "details": {"type": "string"}}
    schema = workspace / "schema.json"
    atomic(schema, {"type": "object", "additionalProperties": False,
                    "properties": props, "required": list(props)})
    prompts = [
        "Read AGENTS.md. Execute pwd. Write probe.txt containing exactly sandbox-probe-ok, then read it. "
        "Run python3 research.py theorem-search --query 'finite group of prime order is cyclic' --limit 1. "
        "Report actual shell/write/retrieval outcomes as structured JSON. If blocked, report the exact error "
        "without escalation or workaround. Do not use MCP or spawn agents.",
        "This is the continuation probe. Read probe.txt and confirm it still contains sandbox-probe-ok. "
        "Write resume.txt containing exactly sandbox-resume-ok and read it back. "
        "Do not access the network again. Return structured JSON reporting whether shell and read/write "
        "from the previous turn succeeded; retain the prior actual retrieval result. No edits except "
        "the requested probe artifacts; no MCP, escalation, or delegation."
    ]
    session = None
    turns = []
    from .core import invoke
    for i, prompt in enumerate(prompts):
        output = workspace / f"final-{i}.json"
        events = workspace / f"events-{i}.jsonl"
        cmd = command(config, workspace, "generator", "live" if i == 0 else "disabled", output, schema, session)
        atomic(workspace / f"invocation-{i}.json", {"command": cmd, "codex_home": config["codex_home"]})
        rc = invoke(cmd, workspace, env, prompt, events, workspace / f"stderr-{i}.log")
        parsed = parse_events(events)
        try:
            response = read_json(output)
        except (OSError, ValueError):
            response = None
        turns.append({"returncode": rc, "events": parsed, "response": response})
        if rc or not parsed["completed"] or parsed["failed"] or parsed["mcp_used"] or not parsed["session"]:
            break
        if session and parsed["session"] != session:
            break
        session = parsed["session"]
    probe = workspace / "probe.txt"
    marker_ok = probe.is_file() and probe.read_text().strip() == "sandbox-probe-ok"
    resume_probe = workspace / "resume.txt"
    resume_marker_ok = resume_probe.is_file() and resume_probe.read_text().strip() == "sandbox-resume-ok"
    valid = lambda t: (isinstance(t["response"], dict) and set(t["response"]) == set(props)
                       and all(type(t["response"][k]) is bool for k in ("shell_ok", "write_ok", "retrieval_ok"))
                       and isinstance(t["response"]["details"], str))
    result.update({"live_checked": True, "artifacts": str(workspace), "turns": turns,
                   "workspace_marker_ok": marker_ok,
                   "resume_marker_ok": resume_marker_ok,
                   "core_passed": len(turns) == 2 and marker_ok and resume_marker_ok and all(
                       t["returncode"] == 0 and t["events"]["completed"] and not t["events"]["failed"]
                       and not t["events"]["mcp_used"] and valid(t)
                       and t["response"]["shell_ok"] and t["response"]["write_ok"]
                       for t in turns) and turns[0]["events"]["session"] == turns[1]["events"]["session"]})
    result["retrieval_passed"] = bool(turns and valid(turns[0]) and turns[0]["response"]["retrieval_ok"])
    result["passed"] = result["core_passed"] and result["retrieval_passed"]
    result["note"] = "core_passed covers shell, writes, structured output and resume; retrieval_passed is separate. Neither certifies mathematical correctness or subagent availability."
    atomic(workspace / "doctor.json", result)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description="Rethlas sandboxed Codex mode; no MCP or inference API key.")
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor", help="Check installed capabilities; --live makes two small model calls")
    d.add_argument("--live", action="store_true")
    r = sub.add_parser("run", help="Create a new isolated research run")
    r.add_argument("--problem", required=True, help="Markdown path relative to repository root")
    r.add_argument("--iterations", type=int, default=10)
    r.add_argument("--iterative-improvement", action="store_true")
    r.add_argument("--dry-run", action="store_true", help="Print settings and command without creating a run")
    r = sub.add_parser("resume", help="Continue the recorded generator and pending verification")
    r.add_argument("--run-id", required=True)
    r.add_argument("--iterations", type=int, default=10)
    r.add_argument("--clear-pause", action="store_true")
    r.add_argument("--iterative-improvement", action="store_true", help="Explicitly enable improvement research on an existing run")
    for name in ("status", "pause", "export"):
        r = sub.add_parser(name)
        r.add_argument("--run-id", required=True)
        if name == "export":
            r.add_argument("--output", help="New export directory inside repository; never overwrite")
    args = p.parse_args(argv)
    workflow = Workflow(reporter=terminal_progress if args.command in ("run", "resume") else None)
    try:
        if args.command == "doctor":
            result = doctor(args.live)
            print(json.dumps(result, indent=2))
            return 0 if (result.get("passed", True) and result["version"]["ok"] and result["authentication"]["ok"]) else 1
        if args.command == "run":
            if args.iterations <= 0:
                raise WorkflowError("Iterations must be positive")
            if args.dry_run:
                from .core import inside
                problem = inside(REPO, args.problem)
                if not problem.is_file() or problem.suffix != ".md":
                    raise WorkflowError("Problem must be an existing Markdown file")
                cwd = workflow.root / "RUN_ID" / "generation"
                config = settings()
                manifest = {"settings": config, "problem": str(problem.relative_to(REPO)),
                            "run_id": "RUN_ID", "next_iteration": 0,
                            "generator_session": None,
                            "mode_policy": "improvement" if args.iterative_improvement else "fixed"}
                print_run_settings(manifest, workflow.root / "RUN_ID", args.iterations, "new (dry run)")
                print("Command:")
                print("  " + shlex.join(command(config, cwd, "generator", "live", cwd / "final.txt")))
                print("")
                print("Dry run only; no files or model calls were started.")
                return 0
            run_id = workflow.create(args.problem, iterative_improvement=args.iterative_improvement)
            run, manifest = workflow.load(run_id)
            print_run_settings(manifest, run, args.iterations, "new")
            with ElapsedTimer():
                result = workflow.resume(run_id, args.iterations)
        elif args.command == "resume":
            run, manifest = workflow.load(args.run_id)
            if args.iterative_improvement:
                manifest = {**manifest, "mode_policy": "improvement"}
            print_run_settings(manifest, run, args.iterations, "resume")
            with ElapsedTimer():
                result = workflow.resume(args.run_id, args.iterations, args.clear_pause, args.iterative_improvement)
        elif args.command == "status":
            _, result = workflow.load(args.run_id)
        elif args.command == "pause":
            run, _ = workflow.load(args.run_id)
            atomic(run / "PAUSE_AFTER_TURN", b"Pause requested\n")
            result = {"run_id": args.run_id, "pause": "Will stop after the active agent turn completes"}
        else:
            result = {"export": str(workflow.export(args.run_id, args.output))}
        if args.command in ("run", "resume"):
            print_run_result(result, workflow.path(result["run_id"]), args.iterations)
            return 0 if result["status"] in ("accepted", "paused") else 2
        print(json.dumps(result, indent=2))
        return 0
    except (WorkflowError, OSError, ValueError, KeyError) as exc:
        if args.command in ("run", "resume"):
            print(f"Error: {exc}", file=sys.stderr)
        else:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
