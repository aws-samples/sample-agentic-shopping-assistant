"""Shared ECS cluster, ALB, and WAF for the API service."""

import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_servicediscovery as servicediscovery,
    aws_wafv2 as wafv2,
    aws_s3 as s3,
)
from cdk_nag import NagSuppressions
from constructs import Construct

from components.webapp_waf import WebappWaf


def cluster_name(ns: str) -> str:
    """The stage's ECS cluster's fixed name. The catalog indexing task runs in it by this name."""
    return ns.lower()


class CommonAppStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        allow_ip_list: list[str],
        server_port: int,
        waf_rate_limit: int = 100,
        public_ingress: bool = False,
        **kwargs,
    ):
        super().__init__(scope, construct_id, **kwargs)

        self.cluster = self._create_cluster(vpc)
        self.load_balancer_security_group = self._create_security_group(vpc, allow_ip_list, server_port)
        self.load_balancer = self._create_alb(vpc, self.load_balancer_security_group)

        waf = WebappWaf(self, "WAF", rate_limit=waf_rate_limit)
        wafv2.CfnWebACLAssociation(
            self,
            "WAFAssociation",
            resource_arn=self.load_balancer.load_balancer_arn,
            web_acl_arn=waf.web_acl.attr_arn,
        )

        NagSuppressions.add_resource_suppressions(
            self.load_balancer_security_group,
            [
                {
                    "id": "AwsSolutions-EC23",
                    "reason": (
                        f"Port {server_port} ingress includes internet-wide allowIpList ranges, which Prod accepts only with "
                        "api.allowPublicIngress; WAF fronts the load balancer"
                        if public_ingress else
                        f"Port {server_port} ingress is limited to the VPC CIDR and allowIpList, fronted by WAF"
                    ),
                },
            ],
            True,
        )

    def _create_cluster(self, vpc: ec2.IVpc) -> ecs.Cluster:
        ns: str = self.node.try_get_context("ns")
        return ecs.Cluster(
            self,
            "Cluster",
            cluster_name=cluster_name(ns),
            vpc=vpc,
            default_cloud_map_namespace=ecs.CloudMapNamespaceOptions(
                name=ns.lower(),
                vpc=vpc,
                type=servicediscovery.NamespaceType.DNS_PRIVATE,
                use_for_service_connect=True,
            ),
            enable_fargate_capacity_providers=True,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

    def _create_security_group(
        self, vpc: ec2.IVpc, allow_ip_list: list[str], server_port: int
    ) -> ec2.SecurityGroup:
        security_group = ec2.SecurityGroup(self, "SecurityGroup", vpc=vpc, allow_all_outbound=True)
        security_group.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block), ec2.Port.tcp(server_port), "Allow inbound VPC traffic"
        )
        for ip in allow_ip_list:
            security_group.add_ingress_rule(
                ec2.Peer.ipv6(ip) if ":" in ip else ec2.Peer.ipv4(ip), ec2.Port.tcp(server_port), "Allow inbound traffic from allowlisted IP"
            )
        return security_group

    def _create_alb(
        self, vpc: ec2.IVpc, security_group: ec2.ISecurityGroup
    ) -> elbv2.ApplicationLoadBalancer:
        ns: str = self.node.try_get_context("ns")
        alb = elbv2.ApplicationLoadBalancer(
            self,
            "ALB",
            load_balancer_name=f"{ns}ApiALB",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            internet_facing=True,
            # Long idle timeout so streaming responses are not cut off.
            idle_timeout=cdk.Duration.seconds(150),
            deletion_protection=bool(self.node.try_get_context("isProd")),
            security_group=security_group,
        )
        archive = s3.Bucket(self, "AccessLogs", encryption=s3.BucketEncryption.S3_MANAGED, enforce_ssl=True,
                            block_public_access=s3.BlockPublicAccess.BLOCK_ALL, versioned=True,
                            lifecycle_rules=[s3.LifecycleRule(expiration=cdk.Duration.days(90), noncurrent_version_expiration=cdk.Duration.days(30))],
                            removal_policy=cdk.RemovalPolicy.RETAIN)
        alb.log_access_logs(archive, "alb")
        NagSuppressions.add_resource_suppressions(archive, [{"id": "AwsSolutions-S1", "reason": "Dedicated access-log destination; recursive server access logging is intentionally disabled"}], True)
        cdk.CfnOutput(self, "ALBDns", value=alb.load_balancer_dns_name, description="ALB DNS Name")
        return alb
