#!/usr/bin/env python3
"""Stand up the AgentCore **Gateway** that exposes the AP procurement tools as
MCP, backed by a Lambda. Demonstrates the fifth AgentCore component.

    PYTHONPATH=src deploy/gateway.py up     # Lambda + Cognito authorizer + Gateway + target
    PYTHONPATH=src deploy/gateway.py test    # OAuth token -> MCP tools/list -> call a tool
    PYTHONPATH=src deploy/gateway.py down     # delete gateway, target, lambda, role

Names are fixed so `down` finds what `up` made. Region us-east-1.
"""

import io
import json
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "deploy" / ".gateway_state.json"
REGION = "us-east-1"
GW_NAME = "ap-autopilot-gw"
FN_NAME = "ap-autopilot-tools"
FN_ROLE = "ap-autopilot-tools-role"

sys.path.insert(0, str(ROOT / "scripts"))
from _awsclock import ensure_clock_synced  # noqa: E402

# The four AP tools, as an MCP tool schema (what the agent sees over the Gateway).
TOOL_SCHEMA = {"inlinePayload": [
    {"name": "lookup_purchase_order", "description": "Look up a purchase order by PO number.",
     "inputSchema": {"type": "object", "properties": {"po_number": {"type": "string"}},
                     "required": ["po_number"]}},
    {"name": "lookup_goods_receipt", "description": "Look up the goods receipt for a PO number.",
     "inputSchema": {"type": "object", "properties": {"po_number": {"type": "string"}},
                     "required": ["po_number"]}},
    {"name": "post_to_erp", "description": "Schedule an approved invoice for payment in the ERP.",
     "inputSchema": {"type": "object", "properties": {
         "invoice_id": {"type": "string"}, "amount": {"type": "string"}},
         "required": ["invoice_id"]}},
    {"name": "route_for_approval", "description": "Open a human-review ticket for an invoice.",
     "inputSchema": {"type": "object", "properties": {
         "invoice_id": {"type": "string"}, "reason": {"type": "string"}},
         "required": ["invoice_id"]}},
]}


def _state(d=None):
    if d is not None:
        STATE.write_text(json.dumps(d, indent=2))
        return d
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _lambda_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(ROOT / "deploy" / "gateway_lambda.py", "gateway_lambda.py")
        z.write(ROOT / "data" / "records.json", "records.json")
    return buf.getvalue()


def _deploy_lambda(boto3) -> str:
    iam = boto3.client("iam", region_name=REGION)
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
             "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    try:
        role = iam.create_role(RoleName=FN_ROLE,
                               AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        iam.attach_role_policy(RoleName=FN_ROLE,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        time.sleep(12)  # role propagation
    except iam.exceptions.EntityAlreadyExistsException:
        role = iam.get_role(RoleName=FN_ROLE)["Role"]["Arn"]

    lam = boto3.client("lambda", region_name=REGION)
    code = _lambda_zip()
    try:
        arn = lam.create_function(FunctionName=FN_NAME, Runtime="python3.12", Role=role,
                                  Handler="gateway_lambda.handler",
                                  Code={"ZipFile": code}, Timeout=30, MemorySize=256)["FunctionArn"]
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName=FN_NAME, ZipFile=code)
        arn = lam.get_function(FunctionName=FN_NAME)["Configuration"]["FunctionArn"]
    return arn


def up() -> int:
    ensure_clock_synced(REGION)
    import boto3
    from bedrock_agentcore_starter_toolkit.operations.gateway import GatewayClient

    st = _state()
    print("deploying tools Lambda…")
    fn_arn = _deploy_lambda(boto3)
    st["lambda_arn"] = fn_arn
    print(f"  lambda: {fn_arn}")

    _state(st)
    gw = GatewayClient(region_name=REGION)
    print("creating Cognito OAuth authorizer…")
    cog = gw.create_oauth_authorizer_with_cognito(GW_NAME)
    st["client_info"] = cog["client_info"]
    _state(st)

    print("creating MCP gateway…")
    gateway = gw.create_mcp_gateway(name=GW_NAME, authorizer_config=cog["authorizer_config"])
    st["gateway_id"] = gateway["gatewayId"]
    st["gateway_url"] = gateway["gatewayUrl"]
    st["gateway_role"] = gateway["roleArn"]
    _state(st)

    # The target uses the GATEWAY_IAM_ROLE credential provider, so the gateway's
    # own role is granted lambda:InvokeFunction by the toolkit. The role's trust
    # policy can take a bit to propagate — retry the AssumeRole race with backoff.
    print("attaching Lambda target (4 AP tools)…")
    target = None
    for attempt in range(8):
        try:
            target = gw.create_mcp_gateway_target(
                gateway=gateway, name="ap-tools", target_type="lambda",
                target_payload={"lambdaArn": fn_arn, "toolSchema": TOOL_SCHEMA})
            break
        except Exception as e:
            if "AssumeRole" in str(e) and attempt < 7:
                print(f"  trust policy not propagated yet, retrying ({attempt+1}/8)…")
                time.sleep(15)
            else:
                raise
    st["target_id"] = target["targetId"]
    _state(st)
    print(f"\n✅ gateway ready: {st['gateway_url']}")
    print("   test:  PYTHONPATH=src deploy/gateway.py test")
    return 0


def test() -> int:
    ensure_clock_synced(REGION)
    from bedrock_agentcore_starter_toolkit.operations.gateway import GatewayClient
    from strands.tools.mcp import MCPClient
    from mcp.client.streamable_http import streamablehttp_client

    st = _state()
    gw = GatewayClient(region_name=REGION)
    token = gw.get_access_token_for_cognito(st["client_info"])
    url = st["gateway_url"]

    client = MCPClient(lambda: streamablehttp_client(
        url, headers={"Authorization": f"Bearer {token}"}))
    with client:
        tools = client.list_tools_sync()
        names = [t.tool_name for t in tools]
        print("MCP tools exposed by the gateway:", names)
        # call one tool through the gateway
        result = client.call_tool_sync(
            tool_use_id="t1",
            name=next(n for n in names if n.endswith("lookup_purchase_order")),
            arguments={"po_number": "PO-4500"})
        print("\nlookup_purchase_order(PO-4500) via gateway ->")
        for block in result["content"]:
            if "text" in block:
                print(json.dumps(json.loads(block["text"]), indent=2)[:600])
    return 0


def down() -> int:
    ensure_clock_synced(REGION)
    import boto3
    from bedrock_agentcore_starter_toolkit.operations.gateway import GatewayClient
    st = _state()
    gw = GatewayClient(region_name=REGION)
    if st.get("gateway_id"):
        try:
            gw.cleanup_gateway(st["gateway_id"], client_info=st.get("client_info"))
            print("deleted gateway + target + Cognito")
        except Exception as e:
            print(f"gateway cleanup: {e}")
    lam = boto3.client("lambda", region_name=REGION)
    try:
        lam.delete_function(FunctionName=FN_NAME); print("deleted lambda")
    except Exception as e:
        print(f"lambda: {e}")
    iam = boto3.client("iam", region_name=REGION)
    try:
        iam.detach_role_policy(RoleName=FN_ROLE,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        iam.delete_role(RoleName=FN_ROLE); print("deleted role")
    except Exception as e:
        print(f"role: {e}")
    STATE.unlink(missing_ok=True)
    print("✅ gateway torn down")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    return {"up": up, "test": test, "down": down}.get(cmd, lambda: (print(__doc__), 2)[1])()


if __name__ == "__main__":
    raise SystemExit(main())
