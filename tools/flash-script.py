#!/usr/bin/env python3
"""Answer a large-data question through Flash's supervised script protocol.

The command copies allowed inputs into a temporary directory, lets Flash write
bounded Python scripts, and uses Jev to inspect the results. Deterministic code
decides whether to return an answer or hand the task to the configured next
desk. When Flash is switched off, the command reads no input and returns that
handoff immediately.

    flash-script.py "<question>" <file-or-folder> [...] [--json]

Exit codes: 0 answered and grounded, 4 hand off (reason printed), 2 usage or environment problem.
Completed and switched-off attempts append a row to out/flash-script-runs.jsonl.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(REPO, "tools")
sys.path.insert(0, TOOLS)
import flashlib
RUNS_LOG = os.path.join(REPO, "out", "flash-script-runs.jsonl")
FLASH_URL = os.environ.get("CARR_FLASH_URL", "http://127.0.0.1:8000")
FLASH_MODEL = os.environ.get("CARR_FLASH_MODEL", "qwen3.8-flash-next")

MAX_RUNS, OUT_CLIP, RUN_TIMEOUT, THINK_TIMEOUT = 3, 2500, 600, 600
SANDBOX_EXEC = "/usr/bin/sandbox-exec"
FILE_LIMIT = 512 * 1024 * 1024  # largest file (and so largest output) a script may write
MAX_TURNS = MAX_RUNS + 2
# Revision 3 capped only compute turns; the uncapped turns that remained ran to 24,576 tokens (428 s once). Every
# script-writing turn is capped here.
COMPUTE_TOKENS = 12288
JEV_TIMEOUT = 60


def append_run(row):
    os.makedirs(os.path.dirname(RUNS_LOG), exist_ok=True)
    with open(RUNS_LOG, "a") as fh:
        fh.write(json.dumps(row) + "\n")

SYSTEM = """You solve data questions whose files are far too large to read. You never see whole files: you
write Python 3 that reads them and prints what you need. Working directory is the data folder.

Each reply must be EXACTLY ONE of:
  (a) one ```python block to run. You will see its stdout and stderr (clipped to 2500 characters).
  (b) a line starting with FINAL: followed by only the answer, in the format the question asks.

Rules:
- Explore before you compute: print a few lines, distinct formats, counts of odd rows. Data may be messy.
- Print small results, never whole files.
- Check your answer with a second, independent method when you can.
- For judgments code cannot make (meaning of free text), a script may do `from flashlib import llm` and call
  llm(prompt) -> str, which asks a capable language model. Batch many items per call (e.g. 50 numbered lines,
  ask for one label per line) and keep calls under ~100.
- Standard library only.
- If the answer is too long to read back in 2500 characters (a long list), have the script write the exact final
  answer to a file in the working directory, e.g. final_answer.json, and then reply FINAL: @final_answer.json"""

COVERAGE = (" Also print how many records your rules could not handle (with 3 of them), and for a classification, "
            "3 example lines for each label.")
DELEGATE = (" Do not work out labels in your head: send items your rules cannot settle to llm() from the script, "
            "as distinct phrases in batches.")
TOTAL_RX = re.compile(r"\b(?:lines|rows|records|entries)\s*[:=]\s*(\d+)", re.I)


def _lib(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "ops", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------- Flash ----------

def chat(messages, *, max_tokens=COMPUTE_TOKENS, think=True, timeout=THINK_TIMEOUT):
    """One Flash turn -> (visible text, finish_reason, completion tokens, reasoning)."""
    body = {"model": FLASH_MODEL, "max_tokens": max_tokens, "messages": messages}
    if not think:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(f"{FLASH_URL}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with flashlib.request_scope(FLASH_URL), urllib.request.urlopen(req, timeout=timeout) as r:
        reply = json.load(r)
    choice = reply["choices"][0]
    msg = choice["message"]
    content = msg.get("content") or ""
    for call in msg.get("tool_calls") or []:  # a tool call the server split out of the text
        args = (call.get("function") or {}).get("arguments", "")
        try:
            args = json.loads(args) if isinstance(args, str) else args
        except ValueError:
            pass
        if isinstance(args, dict):
            args = args.get("code") or next(iter(args.values()), "")
        content = (content + "\n```python\n" + str(args) + "\n```").strip()
    return content, choice.get("finish_reason"), (reply.get("usage") or {}).get("completion_tokens"), \
        msg.get("reasoning_content") or ""


def extract_code(reply):
    """Flash writes code four ways: a ```python fence, an unterminated fence, tool-call markup, or bare code."""
    if "</think>" in reply:
        reply = reply.split("</think>")[-1]
    end = r"(?:</parameter>|</function>|</invoke>|</tool_call>|</python>|\Z)"
    for pat in (r"```(?:python|py)?\s*\n(.*?)```", r"```(?:python|py)?\s*\n(.*)\Z",
                r"<function=python>\s*\n?(?:<parameter=[^>]*>\s*\n?)?(.*?)" + end,
                r"<parameter=[^>]*>\s*\n?(.*?)" + end, r"<python>\s*\n?(.*?)" + end):
        m = re.search(pat, reply, re.S)
        if m and m.group(1).strip():
            return m.group(1).strip()
    lines = [x for x in reply.strip().splitlines() if x.strip()]
    if lines and not reply.lstrip().startswith("FINAL") and \
            sum(bool(re.match(r"\s*(import |from |print\(|for |with |if |def |[A-Za-z_]\w*\s*=)", x)) for x in lines) \
            >= max(2, len(lines) // 2):
        return reply.strip()
    return None


# ---------- the working folder ----------

def preview(work, names):
    out = []
    for name in names:
        p = os.path.join(work, name)
        if os.path.isdir(p):
            files = sorted(os.listdir(p))
            if not files:
                out.append(f"{name}/ (empty folder)")
                continue
            first = next((f for f in files if os.path.isfile(os.path.join(p, f))), None)
            out.append(f"{name}/ (folder, {len(files)} entries: {files[0]} .. {files[-1]}"
                       + (f"; first file {first}, first 6 lines:)" if first else "; no plain files at the top)"))
            if first:
                with open(os.path.join(p, first), errors="replace") as fh:
                    out.append("".join(line for _, line in zip(range(6), fh))[:1200])
        else:
            with open(p, errors="replace") as fh:
                n = sum(1 for _ in fh)
            out.append(f"{name} ({os.path.getsize(p) / 1e6:.2f} MB, {n} lines; first 5 lines:)")
            with open(p, errors="replace") as fh:
                out.append("".join(line for _, line in zip(range(5), fh))[:1200])
    return "\n".join(out)


def stage(paths, work):
    """Copy the inputs into the throwaway folder; the scripts write only there, never beside the originals."""
    names = []
    for src in paths:
        name = os.path.basename(os.path.normpath(src))
        if name in names or name + "/" in names or name == "flashlib.py" or name.startswith(".flash_"):
            raise ValueError(f"two inputs, or an input and the harness, share the name {name!r}")
        dest = os.path.join(work, name)
        if os.path.isdir(src):
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        names.append(name + ("/" if os.path.isdir(src) else ""))
    shutil.copy2(os.path.join(TOOLS, "flashlib.py"), os.path.join(work, "flashlib.py"))  # scripts cannot read tools/
    return names


def flash_port():
    """The Flash server's port, or None when FLASH_URL is not a loopback address (then no script may call it)."""
    u = urllib.parse.urlparse(FLASH_URL)
    return (u.port or 80) if u.hostname in ("127.0.0.1", "localhost") else None


def sandbox_profile(work):
    """macOS sandbox for a model-written script (independent review of PR #1250): the data it reads is untrusted, so
    a planted line can steer Flash into a script that reaches for secrets. Writes only inside the throwaway folder;
    nothing under the user's home, the shared temp folders or /Users/Shared is readable except the folder and the
    interpreter; the network is closed except the local Flash port that flashlib.llm uses; the only program it may
    start is the interpreter; and it can reach no system service and send no Apple Event, because `open`, the
    keychain and launchd are exits around the network block (second review of PR #1250)."""
    work = os.path.realpath(work)
    home = os.path.realpath(os.path.expanduser("~"))
    interp = os.path.dirname(os.path.dirname(os.path.realpath(sys.executable)))
    reads = sorted({work, interp, *(os.path.realpath(p) for p in (sys.prefix, sys.base_prefix))})
    interpreter_execs = {sys.executable, os.path.realpath(sys.executable)}
    # Framework Python launchers spawn this installed interpreter binary.
    # Admit that exact file; sibling programs stay outside the execution grant.
    framework_python = os.path.join(sys.base_prefix, "Resources", "Python.app", "Contents", "MacOS", "Python")
    if sys.platform == "darwin" and os.path.isfile(framework_python):
        interpreter_execs.add(os.path.realpath(framework_python))
    execs = sorted(interpreter_execs)
    # Homebrew's var/etc hold the local Postgres cluster (data files, pg_hba.conf) and service configs.
    # The sandbox matches resolved paths, so each is resolved first: /etc is a link to /private/etc on macOS, and an
    # unresolved "/etc/ssh" rule matched nothing.
    private = sorted({os.path.realpath(p) for p in (
        home, "/private/tmp", "/private/var/folders", "/Users/Shared", "/opt/homebrew/var", "/opt/homebrew/etc",
        "/usr/local/var", "/usr/local/etc", "/etc/ssh", "/Library/Keychains")})
    # Node needs only openssl.cnf, including its resolved file target when symlinked.
    # Keep this literal exception separate from recursive reads: private/certs stay denied.
    openssl = sorted({path for p in ("/opt/homebrew/etc/openssl@3/openssl.cnf",
                                    "/usr/local/etc/openssl@3/openssl.cnf")
                      if os.path.isfile(p) for path in (p, os.path.realpath(p))})
    openssl_read = ("(allow file-read* " + " ".join(f'(literal "{p}")' for p in openssl) + ")"
                    if openssl else "")
    if any('"' in p or "\\" in p for p in [*private, *reads, *openssl, *execs]):
        raise ValueError("path not expressible in a sandbox profile")
    port = flash_port()
    net = f'(allow network-outbound (remote ip "localhost:{port}"))' if port else ""
    return ("(version 1)(allow default)"
            f"(deny network*){net}"
            "(deny file-read* " + " ".join(f'(subpath "{p}")' for p in private) + ")"
            "(allow file-read* " + " ".join(f'(subpath "{p}")' for p in reads) + ")"
            + openssl_read +
            # path lookups (stat) must work to start the interpreter; contents and listings stay denied
            "(allow file-read-metadata)"
            "(deny file-write*)"
            f'(allow file-write* (subpath "{work}") (literal "/dev/null"))'
            "(deny process-exec*)"
            "(allow process-exec " + " ".join(f'(literal "{p}")' for p in execs) + ")"
            "(deny mach-lookup)"
            "(deny appleevent-send)"
            # a script may signal only itself and its own children, never Flash, Postgres, hub jobs or sessions
            "(deny signal (target others))")


def _limits():  # runs in the child before exec: bound file size (so output) and CPU time
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_LIMIT, FILE_LIMIT))
    resource.setrlimit(resource.RLIMIT_CPU, (RUN_TIMEOUT + 30, RUN_TIMEOUT + 30))


def run_code(code, work, n, *, sandbox=True):
    """Run one model-written script in `work`. Fails closed: with sandbox=True (the only value the CLI can reach) a
    machine without the macOS sandbox gets a refusal instead of an unsandboxed run. The environment is an allowlist
    (no credential can be inherited), output goes to files bounded by RLIMIT_FSIZE, and a timeout kills the whole
    process group, not just the script."""
    path = os.path.join(work, f".flash_script_{n}.py")
    with open(path, "w") as fh:
        fh.write(code)
    argv = [sys.executable, path]
    if sandbox:
        if not os.path.exists(SANDBOX_EXEC):
            return "[refused: no script sandbox on this machine; model-written code does not run unsandboxed]", 0.0
        argv = [SANDBOX_EXEC, "-p", sandbox_profile(work), *argv]
    env = {"PATH": "/usr/bin:/bin", "HOME": work, "TMPDIR": work, "PYTHONPATH": work, "LANG": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1", "CARR_FLASH_URL": FLASH_URL, "CARR_FLASH_MODEL": FLASH_MODEL,
           "CARR_FLASH_PREPARED": "1"}
    out_path, err_path = os.path.join(work, f".flash_out_{n}"), os.path.join(work, f".flash_err_{n}")
    t = time.monotonic()
    with flashlib.activity_scope():
        with open(out_path, "wb") as so, open(err_path, "wb") as se:
            p = subprocess.Popen(argv, cwd=work, stdout=so, stderr=se, stdin=subprocess.DEVNULL, env=env,
                                 start_new_session=True, preexec_fn=_limits)
            try:
                p.wait(timeout=RUN_TIMEOUT)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                p.wait()
    stdout, stderr = _tail(out_path, OUT_CLIP), _tail(err_path, 1200)
    out = (stdout + ("\n[stderr]\n" + stderr if stderr.strip() else "")).strip()
    if timed_out:
        out = (stdout + f"\n[timed out after {RUN_TIMEOUT}s]").strip()
    return out or "[no output]", round(time.monotonic() - t, 1)


def _tail(path, limit):
    with open(path, "rb") as fh:
        fh.seek(max(0, os.path.getsize(path) - limit * 4))
        return fh.read().decode(errors="replace")[-limit:]


NUMBER_RX = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")


def literal_numbers(text):
    """Numbers of three or more significant characters written out in `text` (small ones like 0, 1, 10 are
    everywhere in code and prove nothing)."""
    return {m for m in NUMBER_RX.findall(text or "") if len(m.replace(".", "")) >= 3}


def count_gap(answer, outs):
    """(record total, answer sum) when a dict-of-counts answer sums below a record total a script printed."""
    try:
        a = json.loads(answer)
    except ValueError:
        return None
    if not isinstance(a, dict) or len(a) < 2 or not all(isinstance(v, int) for v in a.values()):
        return None
    got = sum(a.values())
    total = max([int(x) for o in outs for x in TOTAL_RX.findall(o) if int(x) > got], default=None)
    return (total, got) if total else None


# ---------- Jev (fails open: no judgment means no steer, never a crash) ----------

class Jev:
    def __init__(self, judge=None, client=None):
        self.judge, self.client, self.errors = judge, client, 0

    def _ask(self, subject, questions):
        try:
            if self.judge is None:
                self.judge = _lib("jev_judge")
            client = self.client or self.judge._client()
            qs = {k: client.noul(text, true=t, false=f) for k, (text, t, f) in questions.items()}
            a = _lib("jev_semantic").ask(subject, qs, caller="flash-script", version="step-v1", transport=self.judge.judge, timeout=JEV_TIMEOUT, client=client)["answers"]
            return {k: round(float(a[k]["noul"]), 2) for k in questions}
        except Exception:
            self.errors += 1
            return {}

    def pre(self, q, prev):
        return self._ask({"question": q[:6000], "file_preview": prev[:4000]}, {
            "multi_doc": ("Answering needs facts combined across many separate documents or files, so a plan of "
                          "several steps is needed before any counting can happen.",
                          "It needs facts combined across many documents in several steps.",
                          "A single pass over one table or file answers it."),
            "semantic": ("The question asks to sort records into categories that are NOT written anywhere in the "
                         "data as a field value or fixed word; each record's category must be inferred from "
                         "descriptive prose using world knowledge (for example knowing that an endodontist is a "
                         "dentist). Messy formatting, varied number formats or fields in different orders do NOT "
                         "count: those are pattern matching.",
                         "Categories must be inferred from prose with world knowledge.",
                         "The needed values are written in the data, even if formatted inconsistently.")})

    def flags(self, q, out, prev):
        return self._ask({"question": q[:6000], "data_files": prev[:3000], "script_output": out[-4000:]}, {
            "misparsed": ("The output shows records whose printed text plainly contains a value that the script "
                          "reports as missing, unparsed or wrong, i.e. the parsing rule misses a format.",
                          "Printed records contradict what the script says it parsed.",
                          "Nothing printed contradicts the parsing."),
            "exploring": ("The output is an exploration step (sample lines, a table of formats or value "
                          "frequencies, file sizes) rather than a computed answer to the question.",
                          "This output explores the data rather than answering the question.",
                          "This output reports a computed answer to the question."),
            "answer_ready": ("The output prints a final result in the form the question asks for (the number, the "
                             "set of counts, or the list), not only samples, formats or intermediate figures.",
                             "A final result in the asked-for form is printed.",
                             "Only samples, formats or intermediate figures are printed.")})

    def covers_all(self, q, answer, total):
        # Code computes the count gap. Only category exhaustiveness is semantic.
        try:
            categories = list(json.loads(answer))[:40]
        except (ValueError, TypeError):
            return 0.0
        return self._ask({"question": q[:6000], "category_names": categories}, {
            "covers_all": ("Does every record described by the question belong to exactly one of these named categories?",
                           "The categories cover every record exactly once.",
                           "Some records fall outside these categories or can belong to several.")
        }).get("covers_all", 0.0)


# ---------- the loop ----------

def solve(question, work, names, *, chat_fn=chat, jev=None, runner=run_code, say=lambda m: None):
    """Answer `question` over the staged files in `work`. Returns (answer or None, turn log)."""
    checks = _lib("flash_answer_checks")
    jev = jev or Jev()
    prev = preview(work, names)
    first = f"Question: {question}\n\nFiles:\n{prev}"
    log = []
    pre = jev.pre(question, prev)
    on = []  # unvalidated semantic hints are logged for review only
    log.append({"jev_pre": pre, "hints": on})
    coverage = COVERAGE
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": first}]
    seen, outs, scripts = set(), [], []
    gap_checked, think_next, restated = False, True, False
    n = 0
    while n < MAX_TURNS:
        n += 1
        runs = sum(1 for x in log if "run_s" in x)
        t = time.monotonic()
        try:
            reply, finish, used, reasoning = chat_fn(msgs, max_tokens=COMPUTE_TOKENS, think=think_next)
        except (TimeoutError, OSError, urllib.error.URLError) as exc:
            reply, finish, used, reasoning = "", f"timeout: {type(exc).__name__}", None, ""
        except (KeyError, IndexError, TypeError, ValueError) as exc:  # a reply that is not the expected shape
            reply, finish, used, reasoning = "", f"malformed: {type(exc).__name__}", None, ""
        think_next = True
        think = round(time.monotonic() - t, 1)
        code = extract_code(reply)
        fin = re.search(r"^FINAL:\s*(.+)", reply.split("</think>")[-1], re.M | re.S)
        if not reply.strip():
            # never feed an empty assistant turn back: the next replies come back empty too
            log.append({"turn": n, "think_s": think, "empty": True, "finish": finish, "tokens": used})
            say(f"turn {n}: empty reply ({finish})")
            if finish == "stop" and re.search(r"```|^FINAL:", reasoning, re.M) and not restated:
                restated = True  # the answer stayed in the hidden reasoning: say so, free of charge
                n -= 1
                blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", reasoning, re.S)
                finals = re.findall(r"^FINAL:.*$", reasoning, re.M)
                last = finals[-1] if finals and (not blocks or reasoning.rfind(finals[-1]) >
                                                 reasoning.rfind(blocks[-1])) else \
                    ("```python\n" + blocks[-1].strip() + "\n```" if blocks else "")
                msgs.append({"role": "user", "content": "Your answer stayed in your hidden reasoning, so nothing "
                             "reached me. This is the last one you wrote there:\n\n" + last + "\n\nReply with it as "
                             "your visible reply, or with a corrected version. Do not start over."})
                continue
            nudge = "Your reply was empty. Keep planning short and reply now with one short python block or FINAL: <answer>."
            if finish == "length":  # fault 8: the next turn runs without thinking
                think_next = False
                log[-1]["no_think_next"] = True
                nudge = ("Your thinking ran out before you replied, so nothing reached me. This turn runs without "
                         "thinking: write the script directly as one python block, with any plan as short code "
                         "comments. Keep the keyword rules short, a few words per category, and do not list edge "
                         "cases. Print how many records the rules could not handle."
                         + (DELEGATE if coverage.endswith(DELEGATE) else ""))
            msgs.append({"role": "user", "content": nudge})
            continue
        msgs.append({"role": "assistant", "content": reply})
        if code and not fin and runs < MAX_RUNS:
            if code in seen:
                log.append({"turn": n, "think_s": think, "stuck": "repeated identical script"})
                return None, log
            seen.add(code)
            scripts.append(code)
            out, secs = runner(code, work, n)
            say(f"turn {n}: script ran {secs}s")
            log.append({"turn": n, "think_s": think, "tokens": used, "run_s": secs, "out": out[:600]})
            outs.append(out)
            runs += 1
            flags = jev.flags(question, out, prev)
            log[-1]["jev_flags"] = flags  # review advice, no calibrated automatic control
            note = ""
            left = MAX_RUNS - runs
            nxt = ("That was your last script. Reply now with FINAL: <answer>." if left <= 0 else
                   f"Next: another python block ({left} left), or FINAL: <answer>.")
            if "[timed out after" in out:
                note += ("\n\nThat script hit the 600-second limit before finishing. If it calls llm() per record, "
                         "batch 50 distinct items per call instead, and print progress so partial results survive.")
            if left > 0:
                log[-1]["focus"] = "advisory_only"
            msgs.append({"role": "user", "content": f"Output:\n{out}{note}\n\n{nxt}"})
        elif fin:
            answer = fin.group(1).strip()
            m = re.match(r"@([\w.\-]+)\s*$", answer.split("\n")[0])
            if m and "\n" in answer.strip():  # fault 9: FINAL @file followed by the code that writes it
                body = extract_code(answer)
                if body and not os.path.exists(os.path.join(work, m.group(1))):
                    scripts.append(body)
                    out2, secs2 = runner(body, work, f"{n}_final")
                    log.append({"turn": n, "final_script_run": True, "run_s": secs2, "out": out2[:600]})
                answer = "@" + m.group(1)
            if m:
                target = os.path.realpath(os.path.join(work, m.group(1)))
                try:
                    if not target.startswith(os.path.realpath(work) + os.sep):
                        raise OSError("outside the working folder")
                    with open(target) as fh:
                        answer = fh.read().strip()
                    # A script wrote the file, but that alone does not ground it: a script can write constants the
                    # model made up. It counts as script output only when none of its numbers is a literal in any
                    # script this run executed; otherwise the answer checks judge it against printed output alone.
                    if not literal_numbers(answer) & set().union(*(literal_numbers(c) for c in scripts)):
                        outs.append(answer)
                except OSError:
                    msgs.append({"role": "user", "content": f"There is no file {m.group(1)} in the working "
                                 "directory. Reply with FINAL: <answer> or FINAL: @<file your script wrote>."})
                    continue
            gap = count_gap(answer, outs) if not gap_checked else None
            if gap:
                gap_checked = True
                cov = jev.covers_all(question, answer, gap[0])
                log.append({"turn": n, "count_gap": gap, "covers_all": cov, "candidate": answer[:500]})
                log[-1]["review_required"] = True
            support = checks.answer_support(answer, outs)
            log.append({"turn": n, "think_s": think, "tokens": used, "final": answer[:3000], "support": support})
            return answer, log
        else:
            log.append({"turn": n, "think_s": think, "bad_reply": reply[-300:], "finish": finish})
            msgs.append({"role": "user", "content": "Reply with one python block or FINAL: <answer>."})
    return None, log


def main(argv):
    ap = argparse.ArgumentParser(description="Flash answers a question over large data by writing scripts.")
    ap.add_argument("question")
    ap.add_argument("paths", nargs="+", help="data files or folders (copied into a throwaway folder)")
    ap.add_argument("--json", action="store_true", help="print the result row as JSON")
    a = ap.parse_args(argv)
    if flashlib.is_switched_off():
        route = _lib("jev_model_route")
        policy = route.load_policy()
        row = {"at": datetime.now(timezone.utc).isoformat(), "question": a.question[:500],
               "paths": [os.path.abspath(p) for p in a.paths], "answer": "", "support": None,
               "handoff": flashlib.OFF_REASON, "handoff_desk": policy["routes"]["script"]["then"]["desk"],
               "turns": 0, "jev_errors": 0, "secs": 0, "log": [], "detail": flashlib.OFF_REASON}
        append_run(row)
        if a.json:
            print(json.dumps({k: v for k, v in row.items() if k != "log"}))
        else:
            print(f"HAND OFF ({flashlib.OFF_REASON}) to {row['handoff_desk']}")
        return 4
    missing = [p for p in a.paths if not os.path.exists(p)]
    if missing:
        print(f"no such file or folder: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not os.path.exists(SANDBOX_EXEC):
        print("flash-script: no script sandbox on this machine (macOS sandbox-exec); refusing to run model code",
              file=sys.stderr)
        return 2
    if flash_port() is None:
        print(f"flash-script: CARR_FLASH_URL must be a loopback address, not {FLASH_URL}", file=sys.stderr)
        return 2
    route = _lib("jev_model_route")
    t = time.monotonic()
    work = tempfile.mkdtemp(prefix="flash-script-")
    try:
        try:
            names = stage(a.paths, work)
        except ValueError as exc:
            print(f"flash-script: {exc}", file=sys.stderr)
            return 2
        jev = Jev()
        answer, log = solve(a.question, work, names, jev=jev,
                            say=(lambda m: None) if a.json else (lambda m: print(m, file=sys.stderr, flush=True)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    reason = route.handoff_reason(answer, log)
    policy = route.load_policy()
    row = {"at": datetime.now(timezone.utc).isoformat(), "question": a.question[:500],
           "paths": [os.path.abspath(p) for p in a.paths], "answer": (answer or "")[:3000],
           "support": next((e["support"] for e in reversed(log) if "support" in e), None),
           "handoff": reason, "handoff_desk": policy["routes"]["script"]["then"]["desk"] if reason else None,
           "turns": sum(1 for e in log if "turn" in e), "jev_errors": jev.errors,
           "secs": round(time.monotonic() - t, 1), "log": log}
    append_run(row)
    if a.json:  # the full answer: a long list is the answer, and the log row keeps only its first 3,000 characters
        print(json.dumps({**{k: v for k, v in row.items() if k != "log"}, "answer": answer or ""}))
    elif reason:
        print(f"HAND OFF ({reason}) to {row['handoff_desk']}" + (f": Flash answered {answer!r}" if answer else ""))
    else:
        print(answer)
    return 4 if reason else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
