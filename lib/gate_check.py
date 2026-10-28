"""gate_check — the one `check(label, condition, detail)` the selftests share.

Over a hundred selftests and gates each defined their own

    def check(name, cond, detail=""):
        if cond: print(f"  ok    {name}")
        else:    failures.append(name); print(f"  FAIL  {name}")

differing only in the ok/FAIL wording, where the failure is recorded, and
whether a counter moves. A copy that is subtly wrong (a branch that records
nothing, a condition that is never evaluated) makes its whole suite green
without anyone seeing it. One implementation, parameterised on exactly those
differences, keeps each suite's output and exit code byte-identical and gives
the suites one place to be right.

A Check proves itself before it is handed out: it records a known failure and a
known pass, and refuses to be built if it cannot tell them apart. So a helper
mutated to always pass turns every suite that uses it red at startup, instead
of turning them all quietly green.

    from lib.gate_check import Check
    FAILURES: list[str] = []
    check = Check(failures=FAILURES)
    check("the gate refuses a missing file", refused, detail="exit was 0")
"""
from __future__ import annotations

from typing import Any, Callable, MutableMapping


class Check:
    """Callable `check(label, condition, detail)` that prints, records, counts.

    ok / fail      line templates ({label}, {detail}); None prints nothing.
    detail         suffix appended to the line when detail is non-empty (on a
                   failure; also on a pass when detail_on_ok). None disables it.
    detail_block   when set, a failure's detail is printed one line at a time
                   under the FAIL line, each prefixed with this string.
    entry          template for what a failure appends to `failures`.
    failures       list that receives each failed entry; passes likewise.
    counters       {variable_name: "pass"|"fail"|"all"} bumped inside `ns`
                   (the suite's globals()), so its summary code is untouched.
    record         callable(label, condition, detail) run on every call.
    raises         None, or the AssertionError message template raised on a
                   failure after it is printed and recorded.
    """

    def __init__(
        self,
        ok: str | None = "  ok    {label}",
        fail: str | None = "  FAIL  {label}",
        *,
        detail: str | None = " — {detail}",
        detail_on_ok: bool = False,
        detail_block: str | None = None,
        default_detail: Any = "",
        entry: str = "{label}",
        failures: list[Any] | None = None,
        passes: list[Any] | None = None,
        counters: dict[str, str] | None = None,
        ns: MutableMapping[str, Any] | None = None,
        record: Callable[[str, Any, Any], object] | None = None,
        raises: str | None = None,
        _probe: bool = True,
    ) -> None:
        self.ok, self.fail, self.detail = ok, fail, detail
        self.detail_on_ok, self.detail_block = detail_on_ok, detail_block
        self.default_detail, self.entry = default_detail, entry
        self.failures, self.passes = failures, passes
        self.counters, self.ns = counters or {}, ns
        self.record, self.raises = record, raises
        if _probe:
            self._prove_it_can_fail()

    def __call__(self, label: str, condition: Any, detail: Any = None) -> bool:
        if detail is None:
            detail = self.default_detail
        verdict = bool(condition)  # MUTATION-TARGET: the one verdict every suite trusts
        for name, when in self.counters.items():
            if when == "all" or (when == "pass") == verdict:
                self._ns()[name] += 1
        if self.record is not None:
            self.record(label, condition, detail)
        template = self.ok if verdict else self.fail
        if template is not None:
            line = template.format(label=label, detail=detail)
            if self.detail and detail and (self.detail_on_ok or not verdict):
                line += self.detail.format(detail=detail)
            print(line)
        if not verdict and self.detail_block is not None and detail:
            for text in str(detail).splitlines():
                print(self.detail_block + text)
        sink = self.passes if verdict else self.failures
        if sink is not None:
            sink.append(label if verdict else self.entry.format(label=label, detail=detail))
        if not verdict and self.raises is not None:
            raise AssertionError(self.raises.format(label=label, detail=detail))
        return verdict

    def _ns(self) -> MutableMapping[str, Any]:
        if self.ns is None:
            raise RuntimeError("gate_check: counters need ns=globals()")
        return self.ns

    def _prove_it_can_fail(self) -> None:
        """Refuse to exist unless a failing condition is recorded as a failure."""
        seen: list[Any] = []
        probe = Check(ok=None, fail=None, failures=seen, raises=self.raises and "{label}", _probe=False)
        try:
            probe("canary pass", True)
            passed_clean = not seen
            probe("canary fail", False)
            raised = False
        except AssertionError:
            raised = True
        recorded = bool(seen) and seen[0] == "canary fail"
        if not (passed_clean and recorded) or (self.raises is not None and not raised):
            raise RuntimeError("gate_check is broken: a failing check was not recorded as failed")
