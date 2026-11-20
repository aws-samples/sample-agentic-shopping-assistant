"""OpenSearch domain backing the product catalog.

Hosts the catalog's versioned indexes behind one alias, queried by the agent's local search tool
from the Runtime's VPC and loaded by the indexing task (components/catalog_indexer.py).
"""

import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2, aws_iam as iam, aws_opensearchservice as oss, aws_logs as logs, custom_resources as cr
from constructs import Construct

ENGINE_VERSION = oss.EngineVersion.OPENSEARCH_3_5


class OpensearchCluster(Construct):
    def __init__(self, scope: Construct, construct_id: str, *, vpc: ec2.IVpc):
        super().__init__(scope, construct_id)

        self.security_group = ec2.SecurityGroup(self, "OpensearchSecurityGroup", vpc=vpc)
        self.domain = self._create_domain(vpc, self.security_group)

    def _create_domain(self, vpc: ec2.IVpc, security_group: ec2.ISecurityGroup) -> oss.Domain:
        is_prod = bool(self.node.try_get_context("isProd"))

        access_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            principals=[iam.AccountPrincipal(cdk.Stack.of(self).account)],
            actions=["es:*"],
            resources=["*"],
        )

        vpc_subnets = (
            [ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)]
            if is_prod
            else [ec2.SubnetSelection(subnets=[vpc.private_subnets[0]])]
        )

        domain = oss.Domain(
            self,
            "OpensearchDomain",
            vpc=vpc,
            vpc_subnets=vpc_subnets,
            node_to_node_encryption=True,
            encryption_at_rest=oss.EncryptionAtRestOptions(enabled=True),
            enforce_https=True,
            use_unsigned_basic_auth=False,
            access_policies=[access_policy],
            ebs=oss.EbsOptions(
                volume_type=ec2.EbsDeviceVolumeType.GP3,
                volume_size=64 if is_prod else 32,
            ),
            zone_awareness=(
                oss.ZoneAwarenessConfig(enabled=True, availability_zone_count=3) if is_prod else None
            ),
            capacity=oss.CapacityConfig(
                data_nodes=3 if is_prod else 1,
                master_nodes=3 if is_prod else None,
                master_node_instance_type="m6g.large.search" if is_prod else None,
                data_node_instance_type="r6g.large.search",
                multi_az_with_standby_enabled=False,
            ),
            logging=oss.LoggingOptions(
                slow_search_log_enabled=True, slow_index_log_enabled=True, app_log_enabled=True,
                slow_search_log_group=logs.LogGroup(self, "SearchSlowLogs", retention=logs.RetentionDays.ONE_MONTH),
                slow_index_log_group=logs.LogGroup(self, "IndexSlowLogs", retention=logs.RetentionDays.ONE_MONTH),
                app_log_group=logs.LogGroup(self, "ApplicationLogs", retention=logs.RetentionDays.ONE_MONTH),
            ),
            version=ENGINE_VERSION,
            security_groups=[security_group],
            removal_policy=cdk.RemovalPolicy.RETAIN if is_prod else cdk.RemovalPolicy.DESTROY,
        )

        return domain


class NoriPlugin(Construct):
    """Associates the Nori (Korean) analysis plugin with the domain, for a definition with analyzer = "nori".

    Nori is an optional Amazon OpenSearch Service package. Its package ID differs by Region and engine
    version, so the first custom resource looks it up (DescribePackages); the second associates it
    with the domain (AssociatePackage). CloudFormation has no property for either. Association
    finishes asynchronously, after the deploy: scripts/index_catalog.sh checks it's ACTIVE before an
    indexing run.

    The association is retained in every stage, and nothing dissociates the plugin: the service
    refuses while an index still uses it, an index analyzed with Nori keeps serving searches and
    rollbacks after the analyzer changes, and a deleted domain takes its plugin with it.
    """

    PACKAGE_NAME = "analysis-nori"

    def __init__(self, scope: Construct, construct_id: str, *, domain: oss.Domain):
        super().__init__(scope, construct_id)
        engine = ENGINE_VERSION.version  # "OpenSearch_3.5", the form DescribePackages filters on
        describe = cr.AwsSdkCall(
            service="OpenSearch",
            action="describePackages",
            parameters={"Filters": [
                {"Name": "PackageName", "Value": [self.PACKAGE_NAME]},
                {"Name": "EngineVersion", "Value": [engine]},
                {"Name": "PackageType", "Value": ["ZIP-PLUGIN"]},
            ]},
            physical_resource_id=cr.PhysicalResourceId.of(f"{self.PACKAGE_NAME}-{engine}"),
            # Only the ID: a custom resource's response must stay under 4 KB.
            output_paths=["PackageDetailsList.0.PackageID"],
        )
        self.package = cr.AwsCustomResource(
            self,
            "Package",
            on_create=describe,
            on_update=describe,
            # DescribePackages has no resource-level permissions (Service Authorization Reference).
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(actions=["es:DescribePackages"], resources=["*"]),
            ]),
            install_latest_aws_sdk=False,
        )
        self.package_id = self.package.get_response_field("PackageDetailsList.0.PackageID")

        target = {"PackageID": self.package_id, "DomainName": domain.domain_name}
        # Named by the domain and the package, so a new package ID (another engine version) is a
        # replacement: the new one is associated, and the old one stays.
        association_id = cr.PhysicalResourceId.of(f"{domain.domain_name}:{self.package_id}")
        associate = cr.AwsSdkCall(
            service="OpenSearch",
            action="associatePackage",
            parameters=target,
            physical_resource_id=association_id,
            output_paths=["DomainPackageDetails.DomainPackageStatus"],
        )
        self.association = cr.AwsCustomResource(
            self,
            "Association",
            on_create=associate,
            on_update=associate,
            # Association is on the domain alone (Service Authorization Reference).
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(actions=["es:AssociatePackage"], resources=[domain.domain_arn]),
            ]),
            install_latest_aws_sdk=False,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        # After the domain and its own configuration updates (access and log policies), which a
        # package operation can't run alongside.
        self.association.node.add_dependency(domain)
