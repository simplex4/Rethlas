"""Standalone, sandbox-local research commands (Python standard library only)."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
try:
    from .research_contract import validate_candidate, digest as baseline_digest
except ImportError:  # copied standalone into the agent workspace
    from research_contract import validate_candidate, digest as baseline_digest

ENDPOINT = "https://leansearch.net/thm/search"
MAX_DOWNLOAD = 32 * 1024 * 1024
CHANNELS = ("immediate_conclusions", "toy_examples", "counterexamples", "big_decisions",
            "subgoals", "proof_steps", "failed_paths", "verification_reports", "branch_states", "events")


def local(path: str | Path) -> Path:
    root = Path.cwd().resolve()
    raw = Path(path)
    if ".." in raw.parts:
        raise ValueError("parent traversal is not permitted")
    resolved = (root / raw).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("path must remain inside the current run workspace")
    return resolved


def atomic(path: str | Path, data: bytes) -> None:
    target = local(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".research-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def object_file(path: str) -> dict:
    data = json.loads(local(path).read_text())
    if not isinstance(data, dict):
        raise ValueError("input must contain a JSON object")
    return data


def channel_path(channel: str) -> Path:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", channel):
        raise ValueError("invalid memory channel")
    return local(Path("memory") / (channel + ".jsonl"))


def append(channel: str, entry: dict) -> dict:
    target = channel_path(channel)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "entry": entry}
    with target.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return {"channel": channel, "record": record}


def search(query: str, channel: str | None) -> dict:
    terms = Counter(re.findall(r"\w+", query.lower()))
    if not terms:
        raise ValueError("query must contain search terms")
    paths = [channel_path(channel)] if channel else sorted(local("memory").glob("*.jsonl"))
    documents = []
    for path in paths:
        path = local(path)
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            for number, line in enumerate(stream, 1):
                record = json.loads(line)
                words = Counter(re.findall(r"\w+", json.dumps(record["entry"]).lower()))
                documents.append((path.stem, number, record, words))
    matches = []
    for ch, number, record, words in documents:
        score = sum(weight * (1 + math.log(words[term])) / math.sqrt(max(1, sum(words.values())))
                    for term, weight in terms.items() if words[term])
        if score:
            matches.append({"channel": ch, "line": number, "score": score, "record": record})
    matches.sort(key=lambda row: row["score"], reverse=True)
    return {"query": query, "matches": matches[:50]}


def require_search() -> None:
    try:
        turn = object_file("turn.json")
    except (OSError, ValueError) as exc:
        raise ValueError("retrieval unavailable: a valid turn.json with search_mode=live is required") from exc
    if turn.get("search_mode") != "live":
        raise ValueError("retrieval unavailable: this turn does not permit external search")


def turn_identity() -> dict:
    turn = object_file("turn.json")
    run_id, attempt = turn.get("run_id"), turn.get("attempt")
    if not isinstance(run_id, str) or not run_id.strip() or type(attempt) is not int or attempt < 0:
        raise ValueError("turn.json must identify a nonempty run_id and nonnegative integer attempt")
    return {"run_id": run_id, "attempt": attempt}


def https(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("retrieval requires an HTTPS URL without credentials")


class HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def retrieve(url: str, payload: dict | None = None) -> tuple[bytes, str]:
    require_search()
    https(url)
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(url, data=body, headers={"User-Agent": "Rethlas-Sandbox/1", "Content-Type": "application/json"})
    with build_opener(HTTPSRedirect()).open(request, timeout=30) as response:
        final_url = response.geturl()
        https(final_url)
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError("retrieval unavailable: response exceeds 32 MiB limit")
    return data, final_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    for name in ("append", "branch", "checkpoint", "submit", "extract"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--file", required=True)
        if name == "append":
            cmd.add_argument("--channel", required=True)
        if name == "branch":
            cmd.add_argument("--id", required=True)
        if name == "submit":
            cmd.add_argument("--improvement-file")
    cmd = commands.add_parser("search")
    cmd.add_argument("--query", required=True)
    cmd.add_argument("--channel")
    cmd = commands.add_parser("theorem-search")
    cmd.add_argument("--query", required=True)
    cmd.add_argument("--limit", type=int, default=10)
    cmd = commands.add_parser("download")
    cmd.add_argument("--url", required=True)
    cmd.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    try:
        result = execute(args)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        result = {"ok": False, "error": str(exc)}
        if args.command in ("download", "theorem-search"):
            result["status"] = "retrieval-unavailable"
            result["note"] = "Retrieval failure is not evidence that a result or reference does not exist."
        print(json.dumps(result), file=sys.stderr)
        return 1


def execute(args: argparse.Namespace) -> dict:
    if args.command == "init":
        for channel in CHANNELS:
            path = channel_path(channel)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
        return {"channels": list(CHANNELS)}
    if args.command == "append":
        return append(args.channel, object_file(args.file))
    if args.command == "branch":
        return append("branch_states", {"branch_id": args.id, "state": object_file(args.file)})
    if args.command == "search":
        return search(args.query, args.channel)
    if args.command == "checkpoint":
        identity = turn_identity()
        data = local(args.file).read_bytes()
        result = {"path": "checkpoint.md", "sha256": hashlib.sha256(data).hexdigest(), **identity}
        atomic("checkpoint.md", data)
        atomic("checkpoint.meta.json", json.dumps(result).encode())
        return result
    if args.command == "submit":
        identity = turn_identity()
        path = local(args.file)
        data = path.read_bytes()
        if not data.strip():
            raise ValueError("candidate must not be empty")
        turn = object_file("turn.json")
        claim = object_file(args.improvement_file) if getattr(args, "improvement_file", None) else None
        validate_candidate(data.decode('utf-8'), local('statement.md').read_text(), turn.get('mode', 'fixed'), claim)
        result = {"path": str(path.relative_to(Path.cwd().resolve())), "sha256": hashlib.sha256(data).hexdigest(), **identity}
        if turn.get('mode') == 'improvement':
            result.update(claim=claim, baseline_sha256=baseline_digest(object_file('baseline.json')))
        atomic("submission.json", json.dumps(result).encode())
        return result
    if args.command == "theorem-search":
        if not args.query.strip() or len(args.query) > 20000 or not 1 <= args.limit <= 10:
            raise ValueError("query must contain 1 to 20000 characters and limit must be between 1 and 10")
        data, final_url = retrieve(ENDPOINT, {"query": args.query, "num_results": args.limit,
            "task": "Given a math statement, retrieve useful references, such as theorems, lemmas, and definitions, that are useful for solving the given problem."})
        results = json.loads(data)
        if not isinstance(results, list) or any(not isinstance(row, dict) for row in results):
            raise ValueError("theorem endpoint must return a list of objects")
        digest = hashlib.sha256(data).hexdigest()
        path = "references/search-" + digest + ".json"
        atomic(path, data)
        append("events", {"type": "theorem-search", "query": args.query, "url": ENDPOINT, "final_url": final_url, "path": path, "sha256": digest})
        return {"results": results, "count": len(results), "path": path, "sha256": digest}
    if args.command == "download":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}", args.name):
            raise ValueError("name must be a safe basename")
        path = "references/" + args.name
        if local(path).exists():
            raise ValueError("reference already exists; choose another name")
        data, final_url = retrieve(args.url)
        atomic(path, data)
        result = {"path": path, "url": args.url, "final_url": final_url, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        append("events", {"type": "download", **result})
        return result
    if args.command == "extract":
        binary = shutil.which("pdftotext")
        if binary is None:
            raise ValueError("pdftotext is missing; install it separately before extracting PDF references")
        source = local(args.file)
        target = local(str(source) + ".txt")
        if target.exists():
            raise ValueError("extracted text already exists")
        completed = subprocess.run([binary, str(source), "-"], capture_output=True, timeout=120, check=True)
        atomic(target, completed.stdout)
        result = {"path": str(target.relative_to(Path.cwd().resolve())), "source": str(source.relative_to(Path.cwd().resolve())), "sha256": hashlib.sha256(completed.stdout).hexdigest()}
        append("events", {"type": "extract", **result})
        return result
    raise ValueError("unknown command")


if __name__ == "__main__":
    sys.exit(main())
