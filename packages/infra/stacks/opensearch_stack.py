"""The OpenSearch catalog: the domain, the Nori plugin, the catalog bucket and the indexing task.

Everything the indexing job touches lives here, so the job needs no cross-stack export. It runs in
the stage's ECS cluster, which CommonApp creates under a fixed name; a task definition doesn't
reference its cluster, so this stack doesn't depend on CommonApp. scripts/index_catalog.sh reads
the outputs below by their fixed names.
"""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2, aws_s3 as s3
from cdk_nag import NagSuppressions
from constructs import Construct

from components.catalog_indexer import CatalogIndexer
from components.opensearch_cluster import NoriPlugin, OpensearchCluster
from config.loader import Config
from stacks.common_app_stack import cluster_name


class OpensearchStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, vpc: ec2.IVpc, config: Config, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        self.cluster = OpensearchCluster(self, "OpensearchCluster", vpc=vpc)
        is_prod = bool(self.node.try_get_context("isProd"))
        settings = config.tools.product_search
        definition = settings.definition
        # The domain's security group needs no EC23 exception: it has no ingress of its own. The
        # AgentRuntime stack adds TCP 443 from the Runtime's security group, and the indexer TCP 443
        # from its own.

        self.nori = NoriPlugin(self, "Nori", domain=self.cluster.domain) if definition.analyzer == "nori" else None

        # Operators upload catalog files here; only the indexing task reads them.
        self.bucket = s3.Bucket(
            self,
            "CatalogBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            removal_policy=cdk.RemovalPolicy.RETAIN if is_prod else cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=not is_prod,
        )

        self.indexer = CatalogIndexer(
            self,
            "Indexer",
            vpc=vpc,
            domain=self.cluster.domain,
            domain_security_group=self.cluster.security_group,
            bucket=self.bucket,
            definition=definition,
            definition_json=settings.definition_json,
            alias=settings.open_search_index,
            retain=is_prod,
        )

        # Read by scripts/index_catalog.sh, by these names.
        outputs = {
            "CatalogBucketName": self.bucket.bucket_name,
            "IndexerCluster": cluster_name(self.node.try_get_context("ns")),
            "IndexerTaskDefinitionArn": self.indexer.task_definition.task_definition_arn,
            "IndexerSubnets": cdk.Fn.join(",", self.indexer.subnets.subnet_ids),
            "IndexerSecurityGroup": self.indexer.security_group.security_group_id,
            "IndexerLogGroup": self.indexer.log_group.log_group_name,
            "ProductAlias": settings.open_search_index,
            # One replica needs a second data node: Dev has one.
            "IndexReplicas": "1" if is_prod else "0",
            "DomainName": self.cluster.domain.domain_name,
        }
        if self.nori:
            outputs["NoriPackageId"] = self.nori.package_id
        for name, value in outputs.items():
            cdk.CfnOutput(self, name, value=value)

        self._suppress_nag(is_prod)

    def _suppress_nag(self, is_prod: bool) -> None:
        domain_suppressions = [
            {
                "id": "AwsSolutions-OS3",
                "reason": "The domain is VPC-only with an account-scoped access policy; no public IP allowlist applies",
            },
        ]
        if not is_prod:
            # Single-node dev cluster: dedicated masters and zone awareness are
            # enabled by the isProd branch in OpensearchCluster.
            domain_suppressions += [
                {"id": "AwsSolutions-OS4", "reason": "Dev cluster runs a single data node; prod enables dedicated masters"},
                {"id": "AwsSolutions-OS7", "reason": "Dev cluster is single-AZ; prod enables zone awareness"},
            ]
        NagSuppressions.add_resource_suppressions(self.cluster.domain, domain_suppressions, True)

        for node in self.cluster.domain.node.find_all():
            if node.node.id == "CustomResourcePolicy" and "ESLogGroupPolicy" in node.node.path:
                NagSuppressions.add_resource_suppressions(node, [{
                    "id": "AwsSolutions-IAM5", "appliesTo": ["Resource::*"],
                    "reason": "CDK log-delivery helper calls only logs:PutResourcePolicy and logs:DeleteResourcePolicy; these account-level APIs require Resource *",
                }], True)
        if self.nori:
            NagSuppressions.add_resource_suppressions(self.nori.package, [{
                "id": "AwsSolutions-IAM5", "appliesTo": ["Resource::*"],
                "reason": "The Nori lookup calls only es:DescribePackages, a read that has no resource-level permissions",
            }], True)

        NagSuppressions.add_resource_suppressions(self.bucket.node.default_child, [{
            "id": "AwsSolutions-S1",
            "reason": "Private, TLS-only and versioned; written only by operators through scripts/index_catalog.sh and read only by the indexing task's role, so no access-log bucket",
        }])
        NagSuppressions.add_resource_suppressions(self.indexer.task_role, [{
            "id": "AwsSolutions-IAM5",
            "reason": "The catalog bucket's catalog/ prefix holds one folder per version; <alias>-* names the alias's versioned indexes; the embedding model's cross-Region profile routes to the foundation model in other Regions",
        }], True)
        NagSuppressions.add_resource_suppressions(self.indexer.task_definition.execution_role, [{
            "id": "AwsSolutions-IAM5",
            "reason": "ECR authorization requires Resource *; image pulls name the asset repository and log writes the task's log group",
        }], True)
        NagSuppressions.add_resource_suppressions(self.indexer.task_definition.node.default_child, [{
            "id": "AwsSolutions-ECS2",
            "reason": "Non-secret settings: the catalog definition, the domain endpoint, the alias and the Region",
        }])

        NagSuppressions.add_stack_suppressions(
            self,
            [
                {
                    "id": "AwsSolutions-IAM4",
                    "reason": "CDK-generated custom resource functions (the domain's access and log policies, the Nori lookup and association, Dev's bucket auto-delete) use AWSLambdaBasicExecutionRole",
                },
            ],
        )
