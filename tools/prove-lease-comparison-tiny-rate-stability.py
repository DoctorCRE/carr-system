#!/usr/bin/env python3
"""Observe deployed lease arithmetic through the authenticated read-only wrapper."""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request


class Unavailable(Exception):
    pass


def synthetic_input(rate):
    option = dict(base_rent_per_sf=[20] * 5, operating_expenses_per_sf=5,
                  free_rent_months=0, free_opex_months=0,
                  buildout_cost_per_sf=100, landlord_ti_per_sf=0)
    return dict(square_feet=1000,
                long_term=dict(option, label="10 Year Lease", term_years=10),
                short_term=dict(option, label="5 Year Lease", term_years=5),
                financing=dict(annual_interest_rate=rate, amortization_years=10))


def check_result(result):
    if not isinstance(result, dict) or result.get("calculation_basis") != "carr-lease-comparison-5vs10-v1":
        raise Unavailable("wrapper did not return lease economics")
    for side in ("long_term", "short_term"):
        option = result.get(side)
        if not isinstance(option, dict):
            raise Unavailable("missing lease option")
        for key in ("annual_operating_expenses", "cost_of_rent", "free_base_rent",
                    "free_operating_expenses", "total_free_rent_and_opex",
                    "estimated_buildout_cost", "landlord_ti_allowance", "tenant_ti_contribution",
                    "total_interest_on_tenant_ti", "total_cost_of_buildout"):
            assert_finite(option.get(key), key)
        schedule = option.get("base_rent_schedule")
        if not isinstance(schedule, list) or len(schedule) != 5:
            raise Unavailable("missing rent schedule")
        for row in schedule:
            if not isinstance(row, dict):
                raise Unavailable("malformed rent schedule")
            for key in ("year", "per_sf", "annual_base_rent"):
                assert_finite(row.get(key), key)
    comparison = result.get("comparison")
    if not isinstance(comparison, dict):
        raise Unavailable("missing comparison")
    for key in ("rent_cost_difference", "lost_concessions_difference", "buildout_cost_difference",
                "additional_cost_of_shorter_term", "additional_years_of_long_term_year_one_rent"):
        assert_finite(comparison.get(key), key)


def assert_finite(value, key):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AssertionError(f"non-finite or missing economic output: {key}")


def observe(origin, wrapper, timeout):
    deadline = time.monotonic() + timeout
    release = None

    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise Unavailable("total deadline exceeded")
        return budget

    def expired(_signum, _frame):
        raise Unavailable("total deadline exceeded")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        request = urllib.request.Request(origin.rstrip("/") + "/release", method="GET")
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        with urllib.request.build_opener(NoRedirect).open(request, timeout=remaining()) as response:
            release = json.loads(response.read(65537))
        if (not isinstance(release, dict) or not isinstance(release.get("git_sha"), str)
                or len(release["git_sha"]) != 40):
            raise Unavailable("release identity unavailable")
        results = []
        for rate in (1e-20, 0, 0.06):
            process = subprocess.Popen(
                [str(Path(wrapper).resolve()), "call", "compare-lease-terms", json.dumps(synthetic_input(rate))],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
            try:
                stdout, _ = process.communicate(timeout=remaining())
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            if process.returncode != 0:
                raise Unavailable("wrapper failed or authentication unavailable")
            result = json.loads(stdout)
            check_result(result)
            results.append(result)
        tiny, zero, ordinary = results
        for side in ("long_term", "short_term"):
            assert zero[side]["total_interest_on_tenant_ti"] == 0, "zero-rate interest differs"
            assert ordinary[side]["total_interest_on_tenant_ti"] == 33224.6, "ordinary-rate control differs"
            for key in ("total_interest_on_tenant_ti", "total_cost_of_buildout"):
                assert abs(tiny[side][key] - zero[side][key]) < 0.005, "tiny-rate convergence differs"
        assert tiny["comparison"] == zero["comparison"], "tiny-rate comparison differs"
        return 0, dict(status="pass", release=release,
                       assertions=["all economic outputs finite", "tiny rate converges to zero",
                                   "zero and ordinary controls match"])
    except AssertionError as error:
        return 1, dict(status="contradiction", release=release, reason=str(error))
    except (Unavailable, OSError, ValueError, subprocess.TimeoutExpired) as error:
        return 78, dict(status="unavailable", release=release, reason=type(error).__name__)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--call-wrapper", required=True)
    parser.add_argument("--timeout-seconds", required=True, type=float)
    args = parser.parse_args()
    if not math.isfinite(args.timeout_seconds) or not 0 < args.timeout_seconds <= 30:
        parser.error("timeout must be positive and at most 30 seconds")
    code, result = observe(args.origin, args.call_wrapper, args.timeout_seconds)
    print(json.dumps(result, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
