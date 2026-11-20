#!/usr/bin/env python3
"""Read-only AWS dependency checks. Never invokes models, creates or changes resources."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/infra"))
# The loader validates the catalog definition with aria_catalog, which imports with the standard
# library alone; preflight runs in the API's environment, which doesn't install it.
sys.path.insert(1, str(Path(__file__).resolve().parents[1] / "packages/catalog/src"))

# The AZ IDs where AgentCore Runtime supports VPC connectivity, by region: the "Supported Availability
# Zones" table in https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.md.
# Creating a Runtime with a subnet in any other AZ fails.
AGENTCORE_VPC_ZONES = {
    "us-east-1": ("use1-az1", "use1-az2", "use1-az4"),
    "us-east-2": ("use2-az1", "use2-az2", "use2-az3"),
    "us-west-1": ("usw1-az1", "usw1-az3"),
    "us-west-2": ("usw2-az1", "usw2-az2", "usw2-az3"),
    "ap-southeast-5": ("apse5-az1", "apse5-az2", "apse5-az3"),
    "ap-south-1": ("aps1-az1", "aps1-az2", "aps1-az3"),
    "ap-south-2": ("aps2-az1", "aps2-az2", "aps2-az3"),
    "ap-northeast-2": ("apne2-az1", "apne2-az2", "apne2-az3"),
    "ap-southeast-1": ("apse1-az1", "apse1-az2", "apse1-az3"),
    "ap-southeast-2": ("apse2-az1", "apse2-az2", "apse2-az3"),
    "ap-southeast-7": ("apse7-az1", "apse7-az2", "apse7-az3"),
    "ap-northeast-1": ("apne1-az1", "apne1-az2", "apne1-az4"),
    "ca-central-1": ("cac1-az1", "cac1-az2", "cac1-az4"),
    "eu-central-1": ("euc1-az1", "euc1-az2", "euc1-az3"),
    "eu-west-1": ("euw1-az1", "euw1-az2", "euw1-az3"),
    "eu-west-2": ("euw2-az1", "euw2-az2", "euw2-az3"),
    "eu-south-1": ("eus1-az1", "eus1-az2", "eus1-az3"),
    "eu-west-3": ("euw3-az1", "euw3-az2", "euw3-az3"),
    "eu-south-2": ("eus2-az1", "eus2-az2", "eus2-az3"),
    "eu-north-1": ("eun1-az1", "eun1-az2", "eun1-az3"),
    "sa-east-1": ("sae1-az1", "sae1-az2", "sae1-az3"),
    "us-gov-west-1": ("usgw1-az1", "usgw1-az2", "usgw1-az3"),
}
# The table's starred AZs, supported only on Runtime platform version V2. The Runtime sets no
# platformVersion, so it runs on the default, V1, and these AZs fail it.
AGENTCORE_VPC_ZONES_V2_ONLY = {"us-east-1": ("use1-az6",)}
# The managed VPC's subnets go in the region's first three available AZs (VpcStack's max_azs).
MANAGED_VPC_AZS = 3


def _private_subnets(ec2, vpc_id: str) -> list[dict]:
    """The subnets of an existing VPC that CDK's lookup types Private, which the Runtime selects
    (PRIVATE_WITH_EGRESS). The rules and their order are the CDK CLI's VPC context provider's."""
    filters = [{"Name": "vpc-id", "Values": [vpc_id]}]
    subnets = ec2.describe_subnets(Filters=filters)["Subnets"]
    tables = ec2.describe_route_tables(Filters=filters)["RouteTables"]
    main = next((t for t in tables if any(a.get("Main") for a in t.get("Associations", []))), {})

    def routes(subnet_id: str) -> list[dict]:
        own = next((t for t in tables if any(a.get("SubnetId") == subnet_id for a in t.get("Associations", []))), main)
        return own.get("Routes", [])

    def subnet_type(subnet: dict) -> str:
        tag = next((t["Value"] for t in subnet.get("Tags", []) if t["Key"] == "aws-cdk:subnet-type"), None)
        if tag is not None:
            return tag
        if subnet.get("MapPublicIpOnLaunch"):
            return "Public"
        own = routes(subnet["SubnetId"])
        if any(r.get("GatewayId", "").startswith("igw-") for r in own):
            return "Public"
        if any((r.get("NatGatewayId") or r.get("TransitGatewayId")) and r.get("DestinationCidrBlock") == "0.0.0.0/0" for r in own):
            return "Private"
        return "Isolated"

    return [s for s in subnets if subnet_type(s) == "Private"]


def check_runtime_zones(ec2, region: str, vpc_id: str) -> list[str]:
    """Fail unless every subnet the Runtime uses is in an AZ ID AgentCore supports; return those IDs.

    An existing VPC (vpc.vpcId): its private subnets with egress. The managed VPC: the region's first
    three available AZs, where VpcStack creates its subnets, whether or not it's deployed yet.
    """
    supported = AGENTCORE_VPC_ZONES.get(region)
    if supported is None:
        raise SystemExit(f"AgentCore doesn't support VPC connectivity in {region}, and the Runtime always runs in the VPC")
    if vpc_id:
        subnets = _private_subnets(ec2, vpc_id)
        if not subnets:
            raise SystemExit(f"{vpc_id} has no private subnets with egress for the Runtime")
        zones = {f"{s['SubnetId']} in {s['AvailabilityZone']}": s["AvailabilityZoneId"] for s in subnets}
        fix = f"The Runtime uses every private subnet with egress in {vpc_id}"
    else:
        available = [z for z in ec2.describe_availability_zones()["AvailabilityZones"] if z["State"] == "available"]
        zones = {z["ZoneName"]: z["ZoneId"] for z in available[:MANAGED_VPC_AZS]}
        fix = (f"The managed VPC uses {region}'s first {MANAGED_VPC_AZS} Availability Zones; "
               "set vpc.vpcId to a VPC whose private subnets are all in supported zones")
    unsupported = [f"{where} ({zone})" for where, zone in zones.items() if zone not in supported]
    if unsupported:
        v2_only = sorted({zone for zone in zones.values() if zone in AGENTCORE_VPC_ZONES_V2_ONLY.get(region, ())})
        note = f"; {', '.join(v2_only)} is supported only on platform version V2, and the Runtime runs on V1" if v2_only else ""
        raise SystemExit(f"The Runtime's subnets must be in AZ IDs AgentCore supports for VPC connectivity in {region} "
                         f"({', '.join(supported)}), and these aren't: {', '.join(unsupported)}{note}. {fix}.")
    return sorted(set(zones.values()))


# The Korean analysis plugin catalog definitions can name (`analyzer = "nori"`), and the engine the
# OpenSearch stack deploys (components/opensearch_cluster.py ENGINE_VERSION).
NORI_PACKAGE = "analysis-nori"
OPENSEARCH_ENGINE = "OpenSearch_3.5"


def check_model(bedrock, model: str) -> str:
    """The foundation model behind `model`: its inference profile is ACTIVE, and the model is authorized and available."""
    if model.split(".")[0] in ("global", "us", "eu", "apac") or ":inference-profile/" in model:
        profile = bedrock.get_inference_profile(inferenceProfileIdentifier=model)
        if profile["status"] != "ACTIVE":
            raise SystemExit(f"Inference profile {model} is not ACTIVE")
        foundation = model.split(".", 1)[1] if not model.startswith("arn:") else profile["models"][0]["modelArn"].split("/")[-1]
    else:
        foundation = model
    availability = bedrock.get_foundation_model_availability(modelId=foundation)
    if availability.get("authorizationStatus") != "AUTHORIZED" or availability.get("regionAvailability") != "AVAILABLE":
        raise SystemExit(f"Model access or region availability is not ready for {foundation}")
    return foundation


def check_certificate(acm, arn: str, region: str) -> str:
    """The API certificate's type, once ACM has issued it in the stage's Region.

    The load balancer refuses any other certificate, but only at the end of a deploy, with an error
    about the domain name, signature and key size that doesn't say the certificate isn't issued.
    """
    if arn.split(":")[3:4] != [region]:
        raise SystemExit(f"api.certificateArn must be an ACM certificate in {region}, the stage's Region")
    certificate = acm.describe_certificate(CertificateArn=arn)["Certificate"]
    if certificate["Status"] != "ISSUED":
        raise SystemExit(f"api.certificateArn is {certificate['Status']}, not ISSUED. A DNS-validated certificate is issued "
                         "once its validation record resolves from the domain's authoritative name servers")
    return certificate["Type"]


def check_nori(opensearch) -> str:
    """The region's Nori plugin package for the deployed engine, or SystemExit when it isn't offered."""
    packages = opensearch.describe_packages(Filters=[
        {"Name": "PackageName", "Value": [NORI_PACKAGE]},
        {"Name": "EngineVersion", "Value": [OPENSEARCH_ENGINE]},
        {"Name": "PackageType", "Value": ["ZIP-PLUGIN"]},
    ]).get("PackageDetailsList", [])
    available = [package for package in packages if package.get("PackageStatus") == "AVAILABLE"]
    if not available:
        raise SystemExit(f"The {NORI_PACKAGE} plugin isn't offered for {OPENSEARCH_ENGINE} in this region; "
                         'set analyzer = "standard" in the catalog definition')
    return available[0]["PackageID"]


def main():
    # Imported here, so the checks above load in the scripts' unit tests without boto3 or pydantic.
    import boto3
    from config.loader import load_config

    config = load_config()
    session = boto3.Session(region_name=config.app.region or "ap-northeast-2")
    identity = session.client("sts").get_caller_identity()
    if config.app.account and identity["Account"] != config.app.account:
        raise SystemExit("Credential account does not match app.account")
    if "REPLACE" in config.model_dump_json() or "example.com" in config.model_dump_json():
        raise SystemExit("Replace template resource and DNS placeholders before deployment")
    zones = check_runtime_zones(session.client("ec2"), session.region_name, config.vpc.vpc_id if config.vpc else "")
    bedrock = session.client("bedrock")
    model = config.runtime.model_id
    check_model(bedrock, model)
    certificate = {}
    if config.api.certificate_arn:
        certificate["certificate"] = check_certificate(session.client("acm"), config.api.certificate_arn, session.region_name)
    search = {}
    if config.tools.product_search.provider == "opensearch":
        definition = config.tools.product_search.definition
        check_model(bedrock, definition.embedding_model)
        search["embeddingModel"] = definition.embedding_model
        if definition.analyzer == "nori":
            search["noriPackage"] = check_nori(session.client("opensearch"))
    if config.evaluations.enabled:
        destination = session.client("xray").get_trace_segment_destination()
        if destination.get("Status") != "ACTIVE" or destination.get("Destination") != "CloudWatchLogs":
            raise SystemExit("Transaction Search delivery to CloudWatch Logs is not active")
    print(json.dumps({"account": identity["Account"], "region": session.region_name, "model": model,
                      "runtimeZoneIds": zones, **certificate, **search, "readOnly": True, "invocationVerified": False}))


if __name__ == "__main__":
    main()
