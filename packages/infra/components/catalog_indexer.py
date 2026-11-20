"""The catalog indexing job: a Fargate task in the VPC that loads the catalog into OpenSearch.

The domain is VPC-only, so the job runs beside it, with no time limit. An operator starts it with
`scripts/index_catalog.sh` (`aws ecs run-task`); it has no endpoint. It reads the catalog from the
stage's catalog bucket, embeds it with the definition's model, builds a versioned index, puts the
search pipeline and promotes the alias (packages/indexer).

Its role reads only the bucket's catalog/ prefix, invokes only the embedding model, and manages only
the alias, its versioned indexes and its pipeline. Its own security group gets one rule into the
domain's: TCP 443.
"""

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_iam as iam,
    aws_logs as logs,
    aws_opensearchservice as oss,
    aws_s3 as s3,
)
from aws_cdk.aws_ecr_assets import DockerImageAsset, Platform
from constructs import Construct

from aria_catalog.definition import CatalogDefinition, definition_environment, pipeline_name
from components.images import build_excludes
from components.model_permissions import model_resources

_PACKAGES = Path(__file__).resolve().parents[2]

# Where scripts/index_catalog.sh uploads a catalog: s3://<bucket>/catalog/<version>/<file>.
CATALOG_PREFIX = "catalog/"
CONTAINER_NAME = "indexer"
LOG_STREAM_PREFIX = "indexer"
# The actions the job sends: HEAD and GET to check the version and the alias, PUT to create the
# index and the pipeline, POST to bulk-load, refresh, count and move the alias.
INDEXER_HTTP_ACTIONS = ["es:ESHttpGet", "es:ESHttpHead", "es:ESHttpPut", "es:ESHttpPost"]


def indexer_paths(domain_arn: str, alias: str) -> list[str]:
    """The domain paths the job may call: the alias, its versioned indexes, _aliases and its pipeline."""
    return [
        f"{domain_arn}/{alias}",
        f"{domain_arn}/{alias}-*",
        f"{domain_arn}/_aliases",
        f"{domain_arn}/_search/pipeline/{pipeline_name(alias)}",
    ]


class CatalogIndexer(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        vpc: ec2.IVpc,
        domain: oss.IDomain,
        domain_security_group: ec2.ISecurityGroup,
        bucket: s3.IBucket,
        definition: CatalogDefinition,
        definition_json: str,
        alias: str,
        retain: bool,
    ):
        super().__init__(scope, construct_id)
        stack = cdk.Stack.of(self)
        ns: str = self.node.try_get_context("ns")

        self.security_group = ec2.SecurityGroup(
            self, "SecurityGroup", vpc=vpc, description="Catalog indexing task", allow_all_outbound=True,
        )
        # Outbound: the domain, S3, ECR and CloudWatch Logs through the VPC, and Bedrock through
        # its endpoint or the NAT gateway. No ingress.
        ec2.CfnSecurityGroupIngress(
            self, "DomainIngress", group_id=domain_security_group.security_group_id,
            source_security_group_id=self.security_group.security_group_id,
            ip_protocol="tcp", from_port=443, to_port=443,
            description="The catalog indexing task may load the catalog domain",
        )
        self.subnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)

        self.log_group = logs.LogGroup(
            self, "Logs", retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.RETAIN if retain else cdk.RemovalPolicy.DESTROY,
        )

        self.task_role = iam.Role(
            self,
            "TaskRole",
            description="The catalog indexing task",
            # Only ECS tasks in this account and Region (confused-deputy protection).
            assumed_by=iam.ServicePrincipal(
                "ecs-tasks.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": stack.account},
                    "ArnLike": {"aws:SourceArn": f"arn:{stack.partition}:ecs:{stack.region}:{stack.account}:*"},
                },
            ),
        )
        self.task_role.add_to_policy(iam.PolicyStatement(
            actions=["s3:GetObject"], resources=[bucket.arn_for_objects(f"{CATALOG_PREFIX}*")],
        ))
        self.task_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=model_resources(definition.embedding_model, stack.region, stack.account, stack.partition),
        ))
        self.task_role.add_to_policy(iam.PolicyStatement(
            actions=INDEXER_HTTP_ACTIONS, resources=indexer_paths(domain.domain_arn, alias),
        ))

        image = DockerImageAsset(
            self,
            "Image",
            directory=str(_PACKAGES),
            file="indexer/Dockerfile",
            # The image needs only the indexer and the catalog package it depends on.
            exclude=build_excludes("indexer"),
            asset_name="aria-indexer",
            platform=Platform.LINUX_ARM64,
        )

        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDefinition",
            family=f"{ns.lower()}-catalog-indexer",
            cpu=1024,
            # Holds the parsed catalog; vectors are embedded and loaded one batch at a time.
            memory_limit_mib=4096,
            task_role=self.task_role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        self.task_definition.add_container(
            CONTAINER_NAME,
            image=ecs.ContainerImage.from_docker_image_asset(image),
            essential=True,
            readonly_root_filesystem=True,
            logging=ecs.LogDriver.aws_logs(log_group=self.log_group, stream_prefix=LOG_STREAM_PREFIX),
            environment={
                # The same definition JSON the Runtime gets, in the same values.
                **definition_environment(definition_json),
                "OPENSEARCH_HOST": domain.domain_endpoint,
                "OPENSEARCH_PRODUCT_INDEX": alias,
                "AWS_REGION": stack.region,
            },
        )
