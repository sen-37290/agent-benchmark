#!/usr/bin/env python3
"""run 디렉터리가 공식 SWE-bench + mini-swe-agent 경로를 그대로 탔는지 검사한다.

각 검사는 반증 가능한 단정문이다. 하나라도 FAIL이면 그 run은 리더보드와 비교할 수 없다.

usage:
  python scripts/check_official_conformance.py runs/<run_id>
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# 공식 swebench.yaml이 에이전트에게 제출 금지시키는 부류. 패치에 이런 파일이 있으면
# 스코프 규약을 어긴 것이고, 우리 이전 파이프라인이 sphinx에서 터진 원인이다.
BUILD_FILES = re.compile(
    r"(^|/)(setup\.py|setup\.cfg|pyproject\.toml|tox\.ini|CHANGES|CHANGELOG"
    r"|MANIFEST\.in|requirements[^/]*\.txt|environment\.ya?ml|Makefile|conftest\.py)$"
)
TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$")
REVERSED = "Reversed (or previously applied) patch detected"

results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    results.append((ok, name, detail))


def patch_files(patch: str) -> list[str]:
    return re.findall(r"^diff --git a/(\S+)", patch, re.M)


def stock_swebench_yaml() -> str:
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import minisweagent,pathlib;"
            "print(pathlib.Path(minisweagent.__file__).parent"
            "/'config'/'benchmarks'/'swebench.yaml')",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    # minisweagent가 import 시 배너를 stdout에 찍으므로 마지막 줄만 쓴다
    return Path(out.stdout.strip().splitlines()[-1].strip()).read_text()


def main(run_dir: Path) -> int:
    art = run_dir / "artifacts"
    mini = art / "minisweagent_swebench"
    trajs = sorted(mini.glob("*/*.traj.json"))
    check(bool(trajs), "trajectory 존재", f"{len(trajs)}개")
    if not trajs:
        report()
        return 1

    preds_path = mini / "preds.json"
    check(preds_path.is_file(), "공식 러너의 preds.json 존재 (Harbor 캡처가 아님)")
    preds = json.loads(preds_path.read_text()) if preds_path.is_file() else {}

    stock = stock_swebench_yaml()
    official_markers = [
        "git diff -- path/to/file1",
        "installation, build, packaging, configuration, or setup scripts",
        "DO NOT MODIFY: Tests, configuration files",
        "cat patch.txt",
    ]

    # ── A. config 계약 ────────────────────────────────────────────────────────
    cfgs, versions, steps, costs = [], set(), set(), set()
    for t in trajs:
        info = json.loads(t.read_text())["info"]
        agent = info["config"]["agent"]
        cfgs.append(agent)
        versions.add(info.get("mini_version"))
        steps.add(agent.get("step_limit"))
        costs.add(agent.get("cost_limit"))

    blob = json.dumps(cfgs[0])
    missing = [m for m in official_markers if m in stock and m not in blob]
    check(
        not missing,
        "공식 swebench.yaml의 제출/스코프 지시문이 프롬프트에 살아있음",
        f"누락: {missing}",
    )
    check(steps == {250}, "step_limit == 250 (공식값)", f"{sorted(steps)}")
    check(costs == {3.0}, "cost_limit == 3.0 (공식값)", f"{sorted(costs)}")
    check(len(versions) == 1, "mini-swe-agent 버전 단일", f"{sorted(versions)}")
    check(len({json.dumps(c, sort_keys=True) for c in cfgs}) == 1, "모든 task가 동일 config")

    # ── B. 패치 출처 ──────────────────────────────────────────────────────────
    mismatched, empty, buildish, testish, bad_exit = [], [], [], [], []
    for t in trajs:
        data = json.loads(t.read_text())
        iid = data.get("instance_id") or t.parent.name
        info = data["info"]
        submission = info.get("submission") or ""
        model_patch = (preds.get(iid) or {}).get("model_patch", "")
        if info.get("exit_status") != "Submitted":
            bad_exit.append(f"{iid}:{info.get('exit_status')}")
        if not model_patch.strip():
            empty.append(iid)
            continue
        # 에이전트가 제출한 것과 채점기로 가는 것이 같아야 한다 (사후 재구성 금지)
        if model_patch.strip() and model_patch.strip() not in submission.strip():
            mismatched.append(iid)
        files = patch_files(model_patch)
        if any(BUILD_FILES.search(f) for f in files):
            buildish.append(iid)
        if any(TEST_PATH.search(f) for f in files):
            testish.append(iid)

    check(
        not mismatched,
        "model_patch가 에이전트의 info.submission에서 온 것 (사후 git diff 재구성 아님)",
        f"불일치 {len(mismatched)}: {mismatched[:3]}",
    )
    check(
        not buildish,
        "패치가 빌드/설정 파일을 건드리지 않음 (setup.py/tox.ini/pyproject 등)",
        f"위반 {len(buildish)}: {buildish[:5]}",
    )
    check(
        not testish,
        "패치가 테스트 파일을 건드리지 않음",
        f"위반 {len(testish)}: {testish[:5]}",
    )
    check(not empty, "빈 패치 없음", f"{len(empty)}개: {empty[:5]}")
    check(not bad_exit, "모든 task가 exit_status=Submitted", f"{len(bad_exit)}개: {bad_exit[:5]}")

    # ── C. 채점 위생 ──────────────────────────────────────────────────────────
    logs = list(run_dir.rglob("run_instance.log")) or list((run_dir / "logs").glob("grade.log"))
    rev = sum(Path(p).read_text(errors="replace").count(REVERSED) for p in logs)
    check(rev == 0, "채점 로그에 reversed-hunk 없음", f"{rev}회 (있으면 패치 스코프 오염)")
    failed_apply = sum(
        Path(p).read_text(errors="replace").count("Failed to apply patch to container")
        for p in logs
    )
    check(failed_apply == 0, "git apply가 fallback patch로 넘어가지 않음", f"{failed_apply}회")

    summary = art / "official_summary.json"
    if summary.is_file():
        s = json.loads(summary.read_text())
        check(
            s["submitted_instances"]
            == s["completed_instances"] + s["empty_patch_instances"] + s["error_instances"],
            "official_summary 내부 정합",
            json.dumps({k: v for k, v in s.items() if isinstance(v, int)}),
        )
    report()
    return 0 if all(ok for ok, _, _ in results) else 1


def report() -> None:
    width = max(len(n) for _, n, _ in results)
    for ok, name, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name:<{width}}  {detail}")
    bad = sum(1 for ok, _, _ in results if not ok)
    print(f"\n{len(results) - bad}/{len(results)} PASS" + (f"  — {bad} FAIL" if bad else ""))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1])))
