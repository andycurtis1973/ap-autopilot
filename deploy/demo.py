#!/usr/bin/env python3
"""One-command live demo: deploy AP Autopilot to Amazon Bedrock AgentCore, run
it on real invoices, then tear it all down.

    PYTHONPATH=src deploy/demo.py up          # build (CodeBuild ARM) + deploy Runtime + Memory
    PYTHONPATH=src deploy/demo.py invoke       # invoke the deployed agent on sample invoices
    PYTHONPATH=src deploy/demo.py down          # delete Runtime, ECR repo, and Memory

What gets provisioned:
  • AgentCore Runtime  — hosts the Strands agent (OTEL Observability on)
  • AgentCore Memory   — per-vendor invoice history (duplicate-payment guard)
  • ECR repo + execution role — auto-created by the starter toolkit

The agent uses the AgentCore Code Interpreter at request time (no standing
resource). Names are fixed so `down` always finds what `up` made. Region us-east-1.
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = ROOT / "deploy" / "runtime"
STATE = ROOT / "deploy" / ".demo_state.json"
AGENT_NAME = "ap_autopilot"
REGION = "us-east-1"

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from _awsclock import ensure_clock_synced  # noqa: E402


def _load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _save_state(d: dict) -> None:
    STATE.write_text(json.dumps(d, indent=2))


def _stage_build_context() -> None:
    """Copy the package + ERP records into the Runtime build context."""
    dst = RUNTIME_DIR / "ap_autopilot"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ROOT / "src" / "ap_autopilot", dst,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "data" / "records.json", RUNTIME_DIR / "records.json")
    print("staged build context (ap_autopilot + records.json)")


def up() -> int:
    ensure_clock_synced(REGION)
    from bedrock_agentcore_starter_toolkit import Runtime
    from ap_autopilot.agentcore import AgentCoreMemory

    _stage_build_context()
    state = _load_state()

    print("creating AgentCore Memory (per-vendor history)…")
    mem = AgentCoreMemory(region=REGION)
    state["memory_id"] = mem.memory_id
    print(f"  memory: {mem.memory_id}")

    rt = Runtime()
    import os
    cwd = os.getcwd()
    os.chdir(RUNTIME_DIR)
    try:
        print("configuring Runtime (ARM64 container, OTEL observability on)…")
        rt.configure(
            entrypoint="agent_runtime.py",
            agent_name=AGENT_NAME,
            requirements_file="requirements.txt",
            auto_create_execution_role=True,
            auto_create_ecr=True,
            region=REGION,
        )
        print("launching via CodeBuild (this takes a few minutes)…")
        result = rt.launch(env_vars={"MEMORY_ID": mem.memory_id})
        state["agent_arn"] = getattr(result, "agent_arn", None) or getattr(result, "agent_id", None)
        state["ecr_uri"] = getattr(result, "ecr_uri", None)
    finally:
        os.chdir(cwd)

    _save_state(state)
    print(f"\n✅ deployed. agent_arn: {state.get('agent_arn')}")
    print("   run:  PYTHONPATH=src deploy/demo.py invoke")
    return 0


def _samples() -> list[dict]:
    """A clean invoice and an exception invoice, from ground truth."""
    from ap_autopilot.generate import build_docset
    ds = build_docset(str(ROOT / "data"), write_pdfs=False)
    clean = next(c for c in ds.cases if c.label == "clean")
    exc = next(c for c in ds.cases if c.label == "price_variance")
    return [{"invoice": clean.invoice.to_dict()}, {"invoice": exc.invoice.to_dict()}]


def invoke() -> int:
    """Invoke the deployed Runtime via the data-plane API (works across processes)."""
    ensure_clock_synced(REGION)
    import boto3
    state = _load_state()
    arn = state.get("agent_arn")
    if not arn:
        print("no deployment found — run `deploy/demo.py up` first")
        return 1
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    for i, payload in enumerate(_samples()):
        inv_id = payload["invoice"]["invoice_id"]
        print(f"\n--- invoking deployed agent on sample {i+1} ({inv_id}) ---")
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=f"demo-{inv_id}-{'x'*30}"[:40],
            payload=json.dumps(payload).encode(),
            contentType="application/json", accept="application/json")
        body = resp["response"].read()
        try:
            print(json.dumps(json.loads(body), indent=2, default=str)[:1600])
        except Exception:
            print(body.decode()[:1600])
    return 0


def down() -> int:
    ensure_clock_synced(REGION)
    import boto3
    state = _load_state()
    ctl = boto3.client("bedrock-agentcore-control", region_name=REGION)

    # Runtime — delete by id via the control plane (robust across processes).
    # Match by name so `down` works even if state was lost.
    try:
        for r in ctl.list_agent_runtimes().get("agentRuntimes", []):
            if r.get("agentRuntimeName") == AGENT_NAME:
                ctl.delete_agent_runtime(agentRuntimeId=r["agentRuntimeId"])
                print(f"deleted Runtime {r['agentRuntimeId']}")
    except Exception as e:
        print(f"runtime teardown: {e}")
    # ECR repo (auto-created by the toolkit as bedrock-agentcore-<agent>)
    try:
        boto3.client("ecr", region_name=REGION).delete_repository(
            repositoryName=f"bedrock-agentcore-{AGENT_NAME}", force=True)
        print("deleted ECR repo")
    except Exception as e:
        print(f"ecr teardown: {e}")
    # Memory
    if state.get("memory_id"):
        try:
            from bedrock_agentcore.memory import MemoryClient
            MemoryClient(region_name=REGION).delete_memory(memory_id=state["memory_id"])
            print(f"deleted Memory {state['memory_id']}")
        except Exception as e:
            print(f"memory teardown: {e}")
    # staged context
    shutil.rmtree(RUNTIME_DIR / "ap_autopilot", ignore_errors=True)
    (RUNTIME_DIR / "records.json").unlink(missing_ok=True)
    STATE.unlink(missing_ok=True)
    print("✅ torn down — zero standing cost")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    return {"up": up, "invoke": invoke, "down": down}.get(cmd, lambda: (print(__doc__), 2)[1])()


if __name__ == "__main__":
    raise SystemExit(main())
