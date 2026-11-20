"""ECS Fargate API service that fronts the AgentCore Runtime.

Serves the web client's HTTP contract by calling InvokeAgentRuntime and reading
session history from AgentCore Memory. It runs no agent itself, so it needs
neither VPC data-plane access nor OpenSearch permissions.
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    aws_cognito as cognito,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_certificatemanager as acm,
)
from aws_cdk.aws_ecr_assets import DockerImageAsset, Platform
from cdk_nag import NagSuppressions
from constructs import Construct

from components.identity import Identity
from components.images import build_excludes
from config.loader import Config

_API_DIR = Path(__file__).resolve().parent.parent.parent / "api"


class ApiServiceStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        config: Config,
        cluster: ecs.ICluster,
        load_balancer: elbv2.IApplicationLoadBalancer,
        load_balancer_security_group: ec2.ISecurityGroup,
        agent_runtime_arn: str,
        memory_id: str,
        discovery_url: str = "",
        user_pool: cognito.IUserPool | None = None,
        user_pool_client: cognito.IUserPoolClient | None = None,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)
        identity = Identity.resolve(config, discovery_url, user_pool_client.user_pool_client_id if user_pool_client else "")

        self.config = config
        server_port = config.api.server_port

        log_group = logs.LogGroup(
            self,
            "LogGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.RETAIN if config.is_prod else cdk.RemovalPolicy.DESTROY,
        )

        task_role = self._create_task_role(agent_runtime_arn, memory_id)
        task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDefinition",
            family=f"{config.ns.lower()}-api",
            cpu=512,
            memory_limit_mib=1024,
            task_role=task_role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.of(config.build_environment.cpu_architecture),
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        image = DockerImageAsset(
            self,
            "ApiImage",
            directory=str(_API_DIR.parent),
            file="api/Dockerfile",
            # Built from packages/ for the shared contracts, without the other packages.
            exclude=build_excludes("api"),
            asset_name="aria-api",
            platform=(
                Platform.LINUX_ARM64
                if config.build_environment.cpu_architecture == "ARM64"
                else Platform.LINUX_AMD64
            ),
        )

        container = task_definition.add_container(
            "api",
            image=ecs.ContainerImage.from_docker_image_asset(image),
            essential=True,
            linux_parameters=ecs.LinuxParameters(self, "ApiLinuxParameters", init_process_enabled=True),
            logging=ecs.LogDriver.aws_logs(log_group=log_group, stream_prefix="aria-api"),
            environment={
                "AGENT_RUNTIME_ARN": agent_runtime_arn,
                "AGENTCORE_MEMORY_ID": memory_id,
                "AGENT_RUNTIME_QUALIFIER": "DEFAULT",
                **identity.environment(),
                "AWS_REGION": self.region,
                "CORS_ALLOW_ORIGINS": ",".join(config.api.cors_allow_origins),
                "REQUEST_TIMEOUT_SECONDS": str(config.api.request_timeout),
                "OTEL_SERVICE_NAME": f"{config.ns.lower()}-api",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
                "OTEL_TRACES_EXPORTER": "otlp",
                "OTEL_METRICS_EXPORTER": "none",
                "OTEL_LOGS_EXPORTER": "none",
                "LOG_LEVEL": "INFO",
                "PORT": str(server_port),
            },
            health_check=ecs.HealthCheck(
                command=["CMD", "python", "-c", f"from urllib.request import urlopen; urlopen('http://localhost:{server_port}/ping', timeout=5).read()"],
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(10),
                retries=3,
                start_period=cdk.Duration.seconds(60),
            ),
        )
        collector = task_definition.add_container(
            "otel-collector", image=ecs.ContainerImage.from_registry("public.ecr.aws/aws-observability/aws-otel-collector@sha256:7968fb60db6a2390a47ba6a2df029745638486e285c9b2487da1b722d0855a3e"),
            essential=False, memory_reservation_mib=128,
            logging=ecs.LogDriver.aws_logs(log_group=log_group, stream_prefix="collector"),
            command=["--config=env:AOT_CONFIG_CONTENT"],
            environment={"AOT_CONFIG_CONTENT": "receivers:\n  otlp:\n    protocols:\n      grpc:\n        endpoint: 0.0.0.0:4317\nprocessors:\n  batch: {}\nexporters:\n  awsxray: {}\nservice:\n  pipelines:\n    traces:\n      receivers: [otlp]\n      processors: [batch]\n      exporters: [awsxray]\n"},
        )
        container.add_container_dependencies(ecs.ContainerDependency(container=collector, condition=ecs.ContainerDependencyCondition.START))
        container.add_port_mappings(ecs.PortMapping(container_port=server_port, protocol=ecs.Protocol.TCP))

        service = self._create_service(cluster, task_definition, load_balancer_security_group, server_port)
        self.service = service
        self.log_group = log_group
        self.target_group = self._attach_to_alb(cluster, load_balancer, load_balancer_security_group, service, server_port)

        self._suppress_nag()

    def _create_task_role(self, agent_runtime_arn: str, memory_id: str) -> iam.Role:
        role = iam.Role(self, "TaskRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"))

        # Read-side memory access for session listing/history, plus deletes. Memory always exists.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:ListSessions",
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:GetEvent",
                    "bedrock-agentcore:DeleteEvent",
                    "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:DeleteMemoryRecord",
                ],
                resources=[f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:memory/{memory_id}"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords"], resources=["*"]
            )
        )
        return role

    def _create_service(
        self,
        cluster: ecs.ICluster,
        task_definition: ecs.FargateTaskDefinition,
        load_balancer_security_group: ec2.ISecurityGroup,
        server_port: int,
    ) -> ecs.FargateService:
        security_group = ec2.SecurityGroup(
            self, "ServiceSecurityGroup", vpc=cluster.vpc, allow_all_outbound=True
        )
        security_group.add_ingress_rule(
            ec2.Peer.security_group_id(load_balancer_security_group.security_group_id),
            ec2.Port.tcp(server_port),
            "Allow inbound ALB traffic",
        )

        service = ecs.FargateService(
            self,
            "Service",
            cluster=cluster,
            task_definition=task_definition,
            security_groups=[security_group],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            desired_count=self.config.api.min_tasks,
            min_healthy_percent=100 if self.config.is_prod else 50,
            max_healthy_percent=200,
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
        )

        scaling = service.auto_scale_task_count(min_capacity=self.config.api.min_tasks, max_capacity=self.config.api.max_tasks)
        self.scaling = scaling
        scaling.scale_on_cpu_utilization("CpuScaling", target_utilization_percent=60)
        scaling.scale_on_memory_utilization("MemoryScaling", target_utilization_percent=60)
        return service

    def _attach_to_alb(
        self,
        cluster: ecs.ICluster,
        load_balancer: elbv2.IApplicationLoadBalancer,
        load_balancer_security_group: ec2.ISecurityGroup,
        service: ecs.FargateService,
        server_port: int,
    ) -> None:
        alb = elbv2.ApplicationLoadBalancer.from_application_load_balancer_attributes(
            self,
            "ImportedALB",
            load_balancer_arn=load_balancer.load_balancer_arn,
            security_group_id=load_balancer_security_group.security_group_id,
            vpc=cluster.vpc,
        )
        target_group = elbv2.ApplicationTargetGroup(
            self,
            "ApiTargetGroup",
            vpc=cluster.vpc,
            port=server_port,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            targets=[service],
            health_check=elbv2.HealthCheck(
                enabled=True, path="/ping", protocol=elbv2.Protocol.HTTP
            ),
        )
        # No stickiness: a conversation is pinned to its Runtime session by session ID, not to
        # an API task, so any task can serve any request.

        alb.add_listener(
            "Listener",
            port=443 if self.config.api.certificate_arn else server_port,
            protocol=elbv2.ApplicationProtocol.HTTPS if self.config.api.certificate_arn else elbv2.ApplicationProtocol.HTTP,
            certificates=[acm.Certificate.from_certificate_arn(self, "ApiCertificate", self.config.api.certificate_arn)] if self.config.api.certificate_arn else None,
            ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS if self.config.api.certificate_arn else None,
            default_target_groups=[target_group],
            open=False,
        )
        self.scaling.scale_on_request_count("RequestScaling", requests_per_target=60, target_group=target_group)
        return target_group

    def _suppress_nag(self) -> None:
        NagSuppressions.add_stack_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-IAM5",
                    "reason": (
                        "X-Ray APIs require Resource *. ECR authorization for the task execution role "
                        "also requires *. Memory permissions are scoped to one resource; Runtime uses caller JWTs"
                    ),
                },
                {
                    "id": "AwsSolutions-ECS2",
                    "reason": "Non-secret configuration (runtime ARN, memory id, pool ids) passed as env vars",
                },
            ],
        )
