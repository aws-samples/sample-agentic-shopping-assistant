# AgentCore Runtime: verified and documented behavior

What ARIA relies on about AgentCore Runtime beyond the developer guide's walkthroughs: the environment limits CloudFormation enforces, VPC mode, and where the agent's telemetry lands. "Verified" means observed on a deployed stage; "documented" means stated in the [AgentCore developer guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html) or the API reference.

## Environment variables

- **Documented:** `CreateAgentRuntime` takes at most 50 environment variables, names of 1 to 100 characters and values of at most 5,000 characters ([CreateAgentRuntime](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_CreateAgentRuntime.html)).
- **Verified:** the `AWS::BedrockAgentCore::Runtime` resource schema (`aws cloudformation describe-type --type RESOURCE --type-name AWS::BedrockAgentCore::Runtime`) is stricter: at most 50 variables, names of at most 128 characters, and values of at most **2,048** characters. CloudFormation's early validation rejects a longer value before it changes anything: `expected maxLength: 2048`.

`checked_environment()` in `packages/infra/stacks/agent_runtime_stack.py` checks the stricter of the two at synth, and `aria_catalog.definition_environment()` splits the catalog definition across values of at most 2,048 bytes.

## VPC mode

- **Documented:** VPC connectivity is offered only in some Availability Zone IDs in each Region; a Runtime with a subnet elsewhere fails to create ([VPC connectivity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html)).
- **Documented:** after a Runtime leaves VPC mode or is deleted, its network interfaces (type `agentic_ai`) stay for up to 8 hours.
- **Verified:** in ap-northeast-2, a Runtime in VPC mode on subnets in `apne2-az1` to `az3` deploys and serves turns.
- **Verified:** switching a Runtime from `PUBLIC` to `VPC` updates it in place: its ID, and so its log group, stay the same.
- **Verified:** while those interfaces remain, a security group they use can't be deleted. CloudFormation reports the group's deletion as failed and finishes the stack update without it, so the group stays until the interfaces are released and it's deleted by hand.
- **Verified:** from the private subnets, the agent reaches Bedrock (the model and Embed v4), the Gateway, AgentCore Identity and Memory through the NAT gateway, and CloudWatch Logs through its interface endpoint.

`scripts/preflight.py` checks the Runtime's subnets against the supported AZ IDs (`AGENTCORE_VPC_ZONES`).

## Telemetry

- **Verified:** the agent's spans land in the Runtime's own log group, `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`, in the `spans` stream, with its OpenTelemetry log records in `otel-rt-logs`. With content capture on, a tool span carries the tool's input. The API's and the Gateway Lambdas' spans land in `aws/spans`.

Evaluations read the Runtime's log group (`operations.traceLogGroup`); when tracing a turn end to end, search both.
