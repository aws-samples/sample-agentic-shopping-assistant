#!/usr/bin/env python3
"""CDK app entrypoint.

Deployment order (CDK resolves this from the dependencies below):
    Vpc -> Opensearch -> Auth -> Gateway -> AgentRuntime -> CommonApp -> ApiService
"""

import os

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

from config.loader import load_config
from stacks.agent_runtime_stack import AgentRuntimeStack
from stacks.api_service_stack import ApiServiceStack
from stacks.auth_stack import AuthStack
from stacks.common_app_stack import CommonAppStack
from stacks.evaluations_stack import EvaluationsStack
from stacks.gateway_stack import GatewayStack
from stacks.opensearch_stack import OpensearchStack
from stacks.vpc_stack import VpcStack
from stacks.operations_stack import OperationsStack

def build_app(config, *, offline=False):

    # cdk-nag's AwsSolutionsChecks checks every stack (added at the end).
    app = cdk.App(
        context={
            "ns": config.ns,
            "stage": config.app.stage,
            "isProd": config.is_prod,
            **({"aws:cdk:bundling-stacks": [], f"availability-zones:account={config.app.account or '123456789012'}:region={config.app.region or 'ap-northeast-2'}": [f"{config.app.region or 'ap-northeast-2'}{az}" for az in "abc"]} if offline else {}),
        }
    )

    env = cdk.Environment(
        account=config.app.account or ("123456789012" if offline else os.environ.get("CDK_DEFAULT_ACCOUNT")),
        region=config.app.region or ("ap-northeast-2" if offline else os.environ.get("CDK_DEFAULT_REGION")),
    )

    vpc_stack = VpcStack(
        app,
        f"{config.ns}Vpc",
        vpc_id=config.vpc.vpc_id if config.vpc else None,
        settings=config.vpc,
        env=env,
    )

    # The catalog domain, its bucket and the in-VPC indexing task, for the opensearch provider only.
    opensearch_stack = OpensearchStack(app, f"{config.ns}Opensearch", vpc=vpc_stack.vpc, config=config, env=env) if config.tools.product_search.provider == "opensearch" else None
    if opensearch_stack:
        opensearch_stack.add_stack_dependency(vpc_stack)

    auth_stack = AuthStack(app, f"{config.ns}Auth", settings=config.auth, env=env) if config.auth.provider == "cognito" else None

    # Gateway: targets for tools that need their own auth, plus the interceptor.
    # Publishes its URL to SSM. Product search is a local tool in the agent.
    gateway_stack = GatewayStack(
        app,
        f"{config.ns}Gateway",
        config=config,
        user_pool=auth_stack.user_pool if auth_stack else None,
        user_pool_client=auth_stack.user_pool_client if auth_stack else None,
        agent_client=auth_stack.agent_client if auth_stack else None,
        discovery_url=auth_stack.discovery_url if auth_stack else "",
        env=env,
    ) if config.gateway.mode == "managed" else None
    if auth_stack and gateway_stack:
        gateway_stack.add_stack_dependency(auth_stack)

    # Runtime: agent container + AgentCore Memory, always in the VPC's private subnets.
    # Reads the Gateway URL from SSM at container startup, so it only needs the Gateway
    # to exist before first invoke.
    agent_runtime_stack = AgentRuntimeStack(
        app,
        f"{config.ns}AgentRuntime",
        config=config,
        vpc=vpc_stack.vpc,
        user_pool=auth_stack.user_pool if auth_stack else None,
        user_pool_client=auth_stack.user_pool_client if auth_stack else None,
        discovery_url=auth_stack.discovery_url if auth_stack else "",
        open_search_domain=opensearch_stack.cluster.domain if opensearch_stack else None,
        open_search_security_group=opensearch_stack.cluster.security_group if opensearch_stack else None,
        env=env,
    )
    agent_runtime_stack.add_stack_dependency(vpc_stack)
    if opensearch_stack:
        agent_runtime_stack.add_stack_dependency(opensearch_stack)
    if auth_stack:
        agent_runtime_stack.add_stack_dependency(auth_stack)
    if gateway_stack:
        agent_runtime_stack.add_stack_dependency(gateway_stack)

    common_app_stack = CommonAppStack(
        app,
        f"{config.ns}CommonApp",
        vpc=vpc_stack.vpc,
        allow_ip_list=[str(cidr) for cidr in config.api.allow_ip_list],
        server_port=443 if config.api.certificate_arn else config.api.server_port,
        waf_rate_limit=config.api.waf_rate_limit,
        public_ingress=bool(config.api.broad_ingress),
        env=env,
    )
    common_app_stack.add_stack_dependency(vpc_stack)

    api_service_stack = ApiServiceStack(
        app,
        f"{config.ns}ApiService",
        config=config,
        cluster=common_app_stack.cluster,
        load_balancer=common_app_stack.load_balancer,
        load_balancer_security_group=common_app_stack.load_balancer_security_group,
        agent_runtime_arn=agent_runtime_stack.runtime_arn,
        memory_id=agent_runtime_stack.memory_id,
        discovery_url=auth_stack.discovery_url if auth_stack else "",
        user_pool=auth_stack.user_pool if auth_stack else None,
        user_pool_client=auth_stack.user_pool_client if auth_stack else None,
        env=env,
    )
    api_service_stack.add_stack_dependency(common_app_stack)
    api_service_stack.add_stack_dependency(agent_runtime_stack)

    operations_stack = OperationsStack(app, f"{config.ns}Operations", config=config, api=api_service_stack,
                                       common=common_app_stack, runtime=agent_runtime_stack,
                                       gateway=gateway_stack, search=opensearch_stack, env=env)

    # Optional: continuous scoring of live agent traffic. Off by default because it
    # spends judge-model tokens on every sampled session. Requires Transaction
    # Search to be ACTIVE (aws xray get-trace-segment-destination).
    if config.evaluations.enabled:
        evaluations_stack = EvaluationsStack(
            app,
            f"{config.ns}Evaluations",
            config=config,
            runtime_name=agent_runtime_stack.runtime_name,
            env=env,
        )
        evaluations_stack.add_stack_dependency(agent_runtime_stack)

    for stack in app.node.children:
        if isinstance(stack, cdk.Stack):
            stack.termination_protection = config.is_prod
    cdk.Aspects.of(app).add(AwsSolutionsChecks())

    tags = cdk.Tags.of(app)
    tags.add("namespace", config.ns)
    tags.add("stage", config.app.stage)

    return app


if __name__ == "__main__":
    build_app(load_config()).synth()
