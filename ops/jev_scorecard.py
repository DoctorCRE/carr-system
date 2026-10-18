"""Deterministic execution grades exact tests and mutations. One bounded cached semantic batch proposes fuzzy subcheck scores for review. Semantic scores never become an automatic pass/fail grade."""

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
import flashlib
DEFAULT_SUITE = os.path.join(REPO, "ops", "config", "flash-scorecard-tasks.v1.json")

DEFAULT_ENDPOINT = "http://127.0.0.1:8000"
DEFAULT_MODEL = "qwen3.8-flash-next"
DEFAULT_TIMEOUT = 600.0
DEFAULT_REASONING_EFFORT = "low"

# Harnesses copied from the experiment's own lib/grade.py so grading here is
# comparable to the run that measured this family of checks, not a
# reimplementation that happens to look similar.
_PY_HEADER = '''
import sys
_fails = []; _n = [0]
def check(name, fn):
    _n[0] += 1
    try:
        r = fn()
        if r is False: raise AssertionError("returned False")
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt): raise
        _fails.append(f"{name}: {type(e).__name__}: {e}"[:400])
def raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    except Exception as e:
        raise AssertionError(f"expected {exc.__name__}, got {type(e).__name__}: {e}")
    raise AssertionError(f"expected {exc.__name__}, nothing raised")
'''
_PY_FOOTER = '''
print(f"PASSED {_n[0]-len(_fails)}/{_n[0]}")
for f in _fails: print("FAIL", f)
sys.exit(1 if _fails else 0)
'''
_JS_HEADER = '''
let __n = 0; const __fails = [];
function check(name, fn) { __n++; try { const r = fn(); if (r === false) throw new Error("returned false"); } catch (e) { __fails.push(name + ": " + String((e && e.message) || e).slice(0, 300)); } }
function raises(fn) { try { fn(); } catch (e) { return true; } throw new Error("expected throw, nothing thrown"); }
'''
_JS_FOOTER = '''
console.log(`PASSED ${__n - __fails.length}/${__n}`); for (const f of __fails) console.log("FAIL", f); process.exit(__fails.length ? 1 : 0);
'''

_CODE_BLOCK = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)```", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S)


def _sibling(name):
    """Load a sibling ops/ module by path. ops/ is not a package, and the whole
    point of these files is that they carry no entrypoint, so there is nothing
    to import them as. Same plumbing jev_judge uses to reach typesafe_client."""
    path = os.path.join(REPO, "ops", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError(f"cannot load ops/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_suite(path=DEFAULT_SUITE):
    """The task list from `path`. Returns a plain list of dicts."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data["tasks"] if isinstance(data, dict) else list(data)


def extract_code(text, lang=None):
    """The largest fenced code block, preferring one tagged with `lang`.

    Same heuristic the experiment's own model_client.extract_code used:
    strip a <think> block, then take the biggest fenced block (a low-effort
    local model sometimes emits a short throwaway snippet before the real
    answer, and length is a cheap, deterministic tiebreak).
    """
    text = _THINK.sub("", text or "")
    blocks = _CODE_BLOCK.findall(text)
    if not blocks:
        return text.strip()
    if lang:
        target = lang.lower()
        # Matched both directions so a task's short lang code ("py", "js")
        # matches a model's full fence tag ("python", "javascript") and a
        # full lang name matches a short tag, without hardcoding either
        # spelling.
        preferred = [body for tag, body in blocks
                     if tag and (tag.lower() in target or target in tag.lower())]
        if preferred:
            return max(preferred, key=len)
    return max((body for _, body in blocks), key=len)


def _chat(messages, *, endpoint, model, temperature, reasoning_effort, max_tokens,
          timeout, opener=None):
    """One call to the local OpenAI-compatible /v1/chat/completions endpoint.

    Never raises: HTTP and connection failures come back as
    {"content": "", "error": "..."} so a caller can grade a missing response
    as a failed attempt instead of crashing the whole suite over one call.
    `opener` is for the offline selftest and is not used in production, same
    convention as ops/typesafe_client.py's `ask(..., opener=...)`.
    """
    import urllib.error
    import urllib.request

    body = {"model": model, "messages": messages, "temperature": temperature,
             "max_tokens": max_tokens, "reasoning_effort": reasoning_effort}
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json"})
    send = opener or urllib.request.urlopen
    try:
        with flashlib.request_scope(endpoint, opener=opener), send(request, timeout=timeout) as response:
            resp = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = err.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        return {"content": "", "usage": {}, "error": f"HTTP {err.code}: {detail}"}
    except Exception as exc:
        return {"content": "", "usage": {}, "error": f"{type(exc).__name__}: {exc}"}
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {"content": msg.get("content") or "", "usage": resp.get("usage", {}), "error": None}


# Candidate code never runs in the process that evaluates hidden assertions.
# The worker protocol carries values/exceptions, never assertion counts or a
# completion verdict. JSON tagged containers preserve tuple keys and identity
# without deserializing candidate-controlled pickle into the grader.
_PY_CODEC = '''
def _pack(v, refs, prefix="input:"):
    nodes, pending = {}, []
    known = {id(x): key for key, x in refs.items()}
    def atom(x):
        if x is None or type(x) in (bool, int, float, str): return ["scalar", x]
        if type(x) not in (list, tuple, dict): raise TypeError("unsupported RPC value")
        token = known.get(id(x))
        if token is None:
            token = prefix + str(id(x)); refs[token] = x; known[id(x)] = token
        if token not in nodes:
            nodes[token] = None; pending.append((token, x))
        return ["ref", token]
    root = atom(v)
    while pending:
        token, x = pending.pop()
        if type(x) is dict: nodes[token] = ["dict", [[atom(k), atom(i)] for k,i in x.items()]]
        else: nodes[token] = ["tuple" if type(x) is tuple else "list", [atom(i) for i in x]]
    return ["graph", root, nodes]
def _unpack(v, refs):
    kind = v[0]
    if kind == "scalar": return v[1]
    if kind == "ref": return refs[v[1]]
    if kind != "graph": raise ValueError("invalid RPC value")
    nodes = v[2]
    tuples = {}
    for key, (kind, items) in nodes.items():
        if key in refs: continue
        if kind == "tuple": tuples[key] = items
        else: refs[key] = [] if kind == "list" else {}
    while tuples:
        ready = [key for key, items in tuples.items() if all(x[0] != "ref" or x[1] in refs for x in items)]
        if not ready: raise ValueError("invalid tuple references")
        for key in ready: refs[key] = tuple(_unpack(x, refs) for x in tuples.pop(key))
    for key, (kind, items) in nodes.items():
        if kind == "list": refs[key][:] = [_unpack(x, refs) for x in items]
        elif kind == "dict":
            values = {_unpack(k, refs): _unpack(x, refs) for k,x in items}
            refs[key].clear(); refs[key].update(values)
    return _unpack(v[1], refs)
'''
_PY_WORKER = '''
import sys, json, contextlib, io
''' + _PY_CODEC + '''
_input, _output = sys.stdin, sys.stdout
sys.stdout = sys.stderr = io.StringIO()
import solution
_exports = {k:v for k,v in vars(solution).items() if not k.startswith("_") and callable(v) and k not in {"check", "raises", "sys"}}
_objects = {}
def _returned(value, refs):
    original = next((k for k,v in refs.items() if v is value), None)
    if original is not None: return ["ref", original]
    if value is None or type(value) in (bool,int,float,str,list,tuple,dict): return _pack(value, refs, "result:")
    key = str(id(value)); _objects[key] = value
    return ["object", key]
_output.write(json.dumps(list(_exports)) + "\\n"); _output.flush()
for line in _input:
    refs = {}
    try:
        req = json.loads(line)
        args = _unpack(req["args"], refs); kwargs = _unpack(req["kwargs"], refs)
        fn = _exports[req["name"]] if req["object"] is None else getattr(_objects[req["object"]], req["name"])
        value = fn(*args, **kwargs)
        returned_refs = dict(refs)
        response = {"value":_returned(value, returned_refs)}
    except BaseException as exc:
        returned_refs = dict(refs)
        response = {"error":type(exc).__name__, "message":str(exc)}
    response["updates"] = [[k, "list", [_returned(x, returned_refs) for x in v]] if type(v) is list else
        [k, "dict", [[_returned(a, returned_refs), _returned(b, returned_refs)] for a,b in v.items()]]
        for k,v in refs.items() if type(v) in (list,dict)]
    _output.write(json.dumps(response) + "\\n"); _output.flush()
'''
_PY_PROXY = '''
import subprocess as _subprocess, json as _json, builtins as _builtins, types as _types
''' + _PY_CODEC + '''
_worker = _subprocess.Popen([sys.executable, "-c", WORKER_SOURCE], stdin=_subprocess.PIPE, stdout=_subprocess.PIPE, stderr=_subprocess.DEVNULL, text=True)
def _rpc(name, args, kwargs, obj=None):
    refs = {}
    request = {"name":name, "object":obj, "args":_pack(args, refs), "kwargs":_pack(kwargs, refs)}
    _worker.stdin.write(_json.dumps(request) + "\\n"); _worker.stdin.flush()
    response = _json.loads(_worker.stdout.readline())
    # Return graphs can introduce nodes referenced by the following updates.
    value = response.get("value")
    result = (_Remote(value[1]) if value[0] == "object" else _unpack(value, refs)) if value is not None else None
    for key, kind, items in response.get("updates", []):
        target = refs[key]
        if kind == "list": target[:] = [_unpack(x, refs) for x in items]
        elif kind == "dict":
            values = {_unpack(k, refs):_unpack(v, refs) for k,v in items}
            target.clear(); target.update(values)
    if "error" in response:
        kind = getattr(_builtins, response["error"], RuntimeError)
        if not isinstance(kind, type) or not issubclass(kind, BaseException): kind = RuntimeError
        raise kind(response.get("message", "candidate exception"))
    return result
class _Remote:
    def __init__(self, key): self.key = key
    def __getattr__(self, name): return lambda *args, **kwargs: _rpc(name, args, kwargs, self.key)
def _export(name): return lambda *args, **kwargs: _rpc(name, args, kwargs)
_solution_proxy = _types.ModuleType("solution")
for _name in _json.loads(_worker.stdout.readline()):
    _function = _export(_name)
    setattr(_solution_proxy, _name, _function)
    globals()[_name] = _function
sys.modules["solution"] = _solution_proxy
'''
# Shared graph transport keeps caller-owned objects observable without loading
# candidate code into the assertion-owning grader. Both ends use the same codec.
_JS_CODEC = '''
const _rpcFs = require('fs');
function readLine(fd) {
  const byte = Buffer.alloc(1); const bytes = [];
  while (_rpcFs.readSync(fd, byte, 0, 1) > 0) {
    if (byte[0] === 10) return Buffer.from(bytes).toString('utf8');
    bytes.push(byte[0]);
  }
  return null;
}
function pack(value, refs, prefix) {
  const known = new Map([...refs].map(([key, value]) => [value, key]));
  const nodes = {}; const pending = []; let next = 0;
  function atom(value) {
    if (value === undefined) return ['undefined'];
    if (value === null || typeof value !== 'object') {
      if (typeof value === 'function' || typeof value === 'symbol') throw new TypeError('unsupported RPC value');
      if (typeof value === 'bigint') return ['bigint', String(value)];
      if (typeof value === 'number' && (!Number.isFinite(value) || Object.is(value, -0))) return ['number', Object.is(value, -0) ? '-0' : String(value)];
      return ['scalar', value];
    }
    let key = known.get(value);
    if (key === undefined) {
      do { key = prefix + next++; } while (refs.has(key));
      known.set(value, key); refs.set(key, value);
    }
    if (!Object.hasOwn(nodes, key)) { nodes[key] = null; pending.push([key, value]); }
    return ['ref', key];
  }
  const root = atom(value);
  while (pending.length) {
    const [key, value] = pending.pop();
    nodes[key] = [Array.isArray(value) ? 'array' : 'object',
      Object.keys(value).map(name => [name, atom(value[name])]),
      Array.isArray(value) ? value.length : null];
  }
  return {root, nodes};
}
function unpack(graph, refs) {
  const nodes = Object.entries(graph.nodes);
  function atom(value) {
    if (value[0] === 'ref') {
      if (!refs.has(value[1])) throw new Error('unknown RPC reference');
      return refs.get(value[1]);
    }
    if (value[0] === 'undefined') return undefined;
    if (value[0] === 'bigint') return BigInt(value[1]);
    if (value[0] === 'number') return Number(value[1]);
    if (value[0] === 'scalar') return value[1];
    throw new Error('invalid RPC value');
  }
  // Allocate all nodes before linking them, retaining existing caller identity.
  for (const [key, node] of nodes) {
    if (!refs.has(key)) refs.set(key, node[0] === 'array' ? [] : {});
  }
  for (const [key, [kind, items, length]] of nodes) {
    const target = refs.get(key);
    const names = new Set(items.map(([name]) => name));
    for (const name of Object.keys(target)) if (!names.has(name)) delete target[name];
    if (kind === 'array' && target.length !== length) target.length = length;
    for (const [name, value] of items) {
      const resolved = atom(value);
      // Do not rewrite unchanged properties, including frozen input objects.
      if (!Object.hasOwn(target, name) || !Object.is(target[name], resolved))
        Object.defineProperty(target, name,
          {value:resolved, writable:true, enumerable:true, configurable:true});
    }
  }
  return atom(graph.root);
}
'''
_JS_WORKER = _JS_CODEC + '''
const fs = require('fs');
console.log = console.error = () => {};
const sol = require('./solution.js');
fs.writeSync(1, JSON.stringify({ready:true}) + '\\n');
for (let line; (line = readLine(0)) !== null;) {
  const input = JSON.parse(line);
  const refs = new Map(); const args = unpack(input.args, refs);
  let value, error;
  try { value = sol[input.name](...args); }
  catch (e) { error = String((e && e.message) || e); }
  // Include updates after exceptions and for aliases detached from the arguments.
  fs.writeSync(1, JSON.stringify({graph:pack([args, value, ...refs.values()], refs, 'result:'), error}) + '\\n');
}
'''
_JS_PROXY = _JS_CODEC + '''
const _child = require('child_process').spawn(process.execPath, ['-e', WORKER_SOURCE], {stdio:['pipe','pipe','ignore']});
const _fs = require('fs');
_child.stdin._handle.setBlocking(true); _child.stdout._handle.setBlocking(true);
function _reply() {
  const line = readLine(_child.stdout._handle.fd);
  if (line === null) throw new Error('candidate exited without a value');
  return JSON.parse(line);
}
if (_reply().ready !== true) throw new Error('candidate did not initialize');
const _nativeRequire = require;
const _solutionPath = _nativeRequire.resolve('./solution.js');
require = name => {
  if (_nativeRequire.resolve(name) !== _solutionPath) return _nativeRequire(name);
  return new Proxy({}, {get:(_, method) => (...args) => {
    const refs = new Map();
    _fs.writeSync(_child.stdin._handle.fd, JSON.stringify({name:method, args:pack(args, refs, 'input:')}) + '\\n');
    const reply = _reply();
    const result = unpack(reply.graph, refs);
    if (reply.error !== undefined) throw new Error(reply.error);
    return result[1];
  }});
};
'''


def _run(cmd, cwd, timeout, source=None):
    process = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(source, timeout=timeout)
        return process.returncode, (stdout + stderr)[-3000:]
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        return -9, "TIMEOUT"


def _grade_impl(task, code, workdir, timeout):
    """Run a candidate implementation against the task's hidden `test` source."""
    lang = task.get("lang", "py")
    # Hidden source goes over stdin to the trusted grader, never into a file
    # shared with candidate code. Only this grader evaluates/counts assertions.
    if lang == "js":
        with open(os.path.join(workdir, "solution.js"), "w", encoding="utf-8") as handle:
            handle.write(code)
        footer = '\n_child.kill(); console.log(JSON.stringify({completed:true,total:__n,passed:__n-__fails.length}));\n'
        src = ('const WORKER_SOURCE = ' + json.dumps(_JS_WORKER) + ';\n' + _JS_HEADER + _JS_PROXY + task["test"] + footer + _JS_FOOTER)
        rc, out = _run(["node"], workdir, timeout, src)
    else:
        with open(os.path.join(workdir, "solution.py"), "w", encoding="utf-8") as handle:
            handle.write(code)
        footer = '\nprint(_json.dumps({"completed":True,"total":_n[0],"passed":_n[0]-len(_fails)}))\n_worker.terminate()\n_worker.wait()\n'
        src = ('WORKER_SOURCE = ' + repr(_PY_WORKER) + '\n' + _PY_HEADER + _PY_PROXY + task["test"] + footer + _PY_FOOTER)
        rc, out = _run([sys.executable, "-"], workdir, timeout, src)
    completed = {}
    for line in out.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get('completed') is True:
            completed = item
    total, count = completed.get('total'), completed.get('passed')
    valid = (completed.get('completed') is True and type(total) is int
             and type(count) is int and total > 0 and 0 <= count <= total)
    scoreline = f'PASSED {count}/{total}' if valid else None
    passed = rc == 0 and valid and count == total
    return {"pass": passed, "rc": rc, "subtests": scoreline or "no-score (crash/import error)",
            "detail": out}


def _grade_mutation(task, test_code, workdir, timeout):
    """Run a candidate TEST SUITE against the reference impl and every mutant.

    Passes only if it runs clean against the correct implementation AND kills
    every mutant (fails on it). Mirrors the experiment's mutation grading so a
    write-tests task is scored the same way here as it was when this family of
    checks was measured.
    """
    correct_dir = os.path.join(workdir, "correct")
    os.makedirs(correct_dir, exist_ok=True)
    with open(os.path.join(correct_dir, "solution.py"), "w", encoding="utf-8") as handle:
        handle.write(task["impl"])
    with open(os.path.join(correct_dir, "test_solution.py"), "w", encoding="utf-8") as handle:
        handle.write(test_code)
    rc, out = _run([sys.executable, "-m", "unittest", "-q", "test_solution"], correct_dir, timeout)
    correct_passes = rc == 0

    killed = 0
    mutants = task.get("mutants", [])
    for i, mutant_src in enumerate(mutants):
        mutant_dir = os.path.join(workdir, f"mutant{i}")
        os.makedirs(mutant_dir, exist_ok=True)
        with open(os.path.join(mutant_dir, "solution.py"), "w", encoding="utf-8") as handle:
            handle.write(mutant_src)
        with open(os.path.join(mutant_dir, "test_solution.py"), "w", encoding="utf-8") as handle:
            handle.write(test_code)
        mrc, _ = _run([sys.executable, "-m", "unittest", "-q", "test_solution"], mutant_dir, timeout)
        killed += mrc != 0

    passed = correct_passes and (not mutants or killed == len(mutants))
    return {"pass": passed, "correct_passes": correct_passes,
            "killed": f"{killed}/{len(mutants)}", "detail": out}


def grade_candidate(task, code, *, workdir=None, timeout=60):
    """Grade one candidate against `task`, deterministically. No Jev involved.

    Dispatches on task.get("kind", "impl"): "mutation" tasks grade `code` as a
    test suite (see _grade_mutation); anything else grades it as an
    implementation run against the task's hidden test (see _grade_impl).
    """
    def _do(directory):
        if task.get("kind") == "mutation":
            return _grade_mutation(task, code, directory, timeout)
        return _grade_impl(task, code, directory, timeout)

    if workdir is not None:
        os.makedirs(workdir, exist_ok=True)
        return _do(workdir)
    with tempfile.TemporaryDirectory(prefix="jev-scorecard-") as directory:
        return _do(directory)


def run_task(task, *, endpoint=DEFAULT_ENDPOINT, model=DEFAULT_MODEL, attempts=1,
             timeout=DEFAULT_TIMEOUT, temperature=0.0,
             reasoning_effort=DEFAULT_REASONING_EFFORT, max_tokens=16384,
             chat_opener=None):
    """Run `task` against the local flash server `attempts` times and grade each.

    Each attempt is generated with low reasoning effort at temperature 0 by
    default — the setting the experiment measured this family of checks
    against (jevx/lib/model_client.py) — and graded deterministically by
    running its code against the task's real hidden tests, never by asking
    Jev whether it looks right.

    Returns {"id", "category", "kind", "attempts": [{"pass", ...,
    "chat_error"}], "any_pass": bool, "first_pass": bool}. NEVER raises: a
    chat or grading failure for one attempt is recorded on that attempt
    (pass=False, chat_error/grade_error set) rather than aborting the task.
    """
    attempt_results = []
    for _ in range(max(1, attempts)):
        reply = _chat(
            [{"role": "user", "content": task["prompt"]}],
            endpoint=endpoint, model=model, temperature=temperature,
            reasoning_effort=reasoning_effort, max_tokens=max_tokens,
            timeout=timeout, opener=chat_opener)
        if reply.get("error"):
            attempt_results.append({"pass": False, "chat_error": reply["error"]})
            continue
        code = extract_code(reply["content"], lang=task.get("lang"))
        try:
            grade = grade_candidate(task, code, timeout=min(60, timeout))
        except Exception as exc:
            attempt_results.append({"pass": False, "grade_error": f"{type(exc).__name__}: {exc}",
                                     "usage": reply.get("usage")})
            continue
        attempt_results.append({**grade, "usage": reply.get("usage")})

    return {
        "id": task.get("id"),
        "category": task.get("category", "uncategorized"),
        "kind": task.get("kind", "impl"),
        "attempts": attempt_results,
        "any_pass": any(a.get("pass") for a in attempt_results),
        "first_pass": bool(attempt_results) and bool(attempt_results[0].get("pass")),
    }


def grade_fuzzy(output, subchecks, *, client=None, judge=None):
    """Score free-text `output` against `subchecks`, each an independent yes/no.

    One Noul per sub-check, ALL batched into a single Jev request — several
    independent facts about the same subject, per ops/jev_judge.py's
    doctrine, never one request per sub-check and never a single broad
    question standing in for several.

    Nonempty semantic grading returns verdict="review_required", escalate=True
    and per-subcheck advice. Only an empty checklist returns exact True.
    No probability threshold authorizes a passing grade. NEVER raises.
    """
    judge = judge or _sibling("jev_judge")
    subchecks = list(subchecks)
    if not subchecks:
        return {"check": "scorecard_fuzzy", "verdict": True, "confidence": None,
                "escalate": False, "detail": {"subchecks": {}, "reason": "no sub-checks given"}}

    tsc = client or _sibling("typesafe_client")
    questions = {
        f"c{i}": tsc.noul(
            f"Does `state.output` satisfy this: {subcheck}",
            true="state.output clearly satisfies this",
            false="state.output does not satisfy this, or it is absent")
        for i, subcheck in enumerate(subchecks)
    }
    subject = {"output": (output or "")[:8000]}
    try:
        answer = _sibling("jev_semantic").ask(subject, questions, client=client, caller="jev_scorecard", version="vendor-v1", transport=judge.judge)
    except Exception as exc:
        try:
            judge.record("supervise.scorecard_fuzzy", (output or "")[:200], None, None, error=exc)
        except Exception:
            pass
        return {"check": "scorecard_fuzzy", "verdict": "unavailable", "confidence": None,
                "escalate": True, "detail": {"reason": f"{type(exc).__name__}: {exc}",
                                               "subchecks": {s: None for s in subchecks}}}

    probs = {}
    for i, subcheck in enumerate(subchecks):
        probs[subcheck] = float(answer["answers"][f"c{i}"]["noul"])
    verdict = "review_required"
    escalate = True  # no labeled validation for automatic acceptance
    judge.record("supervise.scorecard_fuzzy", (output or "")[:200], answer, existing_decision=None)
    return {"check": "scorecard_fuzzy", "verdict": verdict, "confidence": None,
            "escalate": escalate, "detail": {"subchecks": probs, "model": answer.get("model")}}


def summarize(results):
    """Pass counts by category, plus an overall row. `results` is run_task() output.

    Returns {"by_category": {category: {"n", "any_pass", "first_pass"}},
    "overall": {"n", "any_pass", "first_pass"}}. Pure arithmetic over facts
    run_task already produced — no judgment, no Jev, per the "keep arithmetic
    in code" doctrine ops/typesafe_client.py states directly.
    """
    by_category = {}
    for row in results:
        bucket = by_category.setdefault(row.get("category", "uncategorized"),
                                          {"n": 0, "any_pass": 0, "first_pass": 0})
        bucket["n"] += 1
        bucket["any_pass"] += int(bool(row.get("any_pass")))
        bucket["first_pass"] += int(bool(row.get("first_pass")))
    overall = {"n": sum(b["n"] for b in by_category.values()),
               "any_pass": sum(b["any_pass"] for b in by_category.values()),
               "first_pass": sum(b["first_pass"] for b in by_category.values())}
    return {"by_category": by_category, "overall": overall}
