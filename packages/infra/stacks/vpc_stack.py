"""VPC with the endpoints required by ECS and AgentCore VPC mode."""

from typing import Optional

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2, aws_logs as logs
from cdk_nag import NagSuppressions
from constructs import Construct
from config.loader import VpcConfig


class VpcStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, vpc_id: Optional[str] = None, settings: VpcConfig | None = None, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.settings = settings or VpcConfig(natGateways=3 if self.node.try_get_context("isProd") else 1)
        if vpc_id:
            self.vpc: ec2.IVpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=vpc_id)
            return

        vpc = self._create_vpc()
        security_group = self._create_endpoint_security_group(vpc)
        self._create_endpoints(vpc, security_group)
        self.vpc = vpc

    def _create_vpc(self) -> ec2.Vpc:
        ns: str = self.node.try_get_context("ns")

        # Prod keeps its flow logs when the stack goes: they are the network record of an incident.
        flow_logs = logs.LogGroup(
            self,
            "FlowLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.RETAIN if self.node.try_get_context("isProd") else cdk.RemovalPolicy.DESTROY,
        )

        return ec2.Vpc(
            self,
            "Vpc",
            vpc_name=ns.lower(),
            # The Runtime's subnets go in these AZs; scripts/preflight.py (MANAGED_VPC_AZS) checks them against AgentCore's list.
            max_azs=3,
            nat_gateways=self.settings.nat_gateways,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    cidr_mask=24,
                    name="ingress",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    map_public_ip_on_launch=False,
                ),
                ec2.SubnetConfiguration(
                    cidr_mask=24,
                    name="egress",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                ),
                ec2.SubnetConfiguration(
                    cidr_mask=24,
                    name="isolated",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                ),
            ],
            flow_logs={
                "cloudwatch": ec2.FlowLogOptions(
                    destination=ec2.FlowLogDestination.to_cloud_watch_logs(flow_logs),
                    traffic_type=ec2.FlowLogTrafficType.ALL,
                )
            },
        )

    def _create_endpoints(self, vpc: ec2.IVpc, security_group: ec2.ISecurityGroup) -> None:
        # S3 gateway endpoint is required for ECR layer pulls in AgentCore VPC
        # mode and is free — it avoids NAT data processing charges.
        vpc.add_gateway_endpoint("S3VpcE", service=ec2.GatewayVpcEndpointAwsService.S3)

        interface_endpoints = {
            "BedrockVpcE": ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
            "EcrApiVpcE": ec2.InterfaceVpcEndpointAwsService.ECR,
            "EcrDockerVpcE": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
            "EcsEndpointVpcE": ec2.InterfaceVpcEndpointAwsService.ECS,
            "EcsAgentVpcE": ec2.InterfaceVpcEndpointAwsService.ECS_AGENT,
            "EcsTelemetryVpcE": ec2.InterfaceVpcEndpointAwsService.ECS_TELEMETRY,
            # Required by AgentCore VPC mode so the runtime can create log streams.
            "LogsVpcE": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
            # The agent resolves the Gateway URL from SSM at container startup.
            "SsmVpcE": ec2.InterfaceVpcEndpointAwsService.SSM,
        }
        names = {"BedrockVpcE": "bedrock", "EcrApiVpcE": "ecr", "EcrDockerVpcE": "ecr-docker", "EcsEndpointVpcE": "ecs", "EcsAgentVpcE": "ecs-agent", "EcsTelemetryVpcE": "ecs-telemetry", "LogsVpcE": "logs", "SsmVpcE": "ssm"}
        unknown = set(self.settings.interface_endpoints) - set(names.values())
        if unknown:
            raise ValueError(f"Unknown VPC endpoints: {unknown}")
        for endpoint_id, service in interface_endpoints.items():
            if names[endpoint_id] not in self.settings.interface_endpoints:
                continue
            vpc.add_interface_endpoint(endpoint_id, service=service, security_groups=[security_group])

    def _create_endpoint_security_group(self, vpc: ec2.IVpc) -> ec2.SecurityGroup:
        security_group = ec2.SecurityGroup(self, "vpcEndpointSecurityGroup", vpc=vpc)
        security_group.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block),
            ec2.Port.tcp(443),
            "Allow internal access for several VPC Endpoint",
        )
        NagSuppressions.add_resource_suppressions(
            security_group,
            [
                {
                    "id": "AwsSolutions-EC23",
                    "reason": "Ingress is scoped to the VPC CIDR on 443 only, not 0.0.0.0/0",
                },
            ],
            True,
        )
        return security_group
