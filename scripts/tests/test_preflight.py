import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from preflight import AGENTCORE_VPC_ZONES, AGENTCORE_VPC_ZONES_V2_ONLY, check_certificate, check_model, check_nori, check_runtime_zones


class FakeEc2:
    """Answers the three read-only calls the check makes; any other call fails with AttributeError."""

    def __init__(self, zones=(), subnets=(), route_tables=()):
        self.zones, self.subnets, self.route_tables = list(zones), list(subnets), list(route_tables)
        self.calls = []

    def describe_availability_zones(self, **kwargs):
        self.calls.append(("describe_availability_zones", kwargs))
        return {"AvailabilityZones": self.zones}

    def describe_subnets(self, **kwargs):
        self.calls.append(("describe_subnets", kwargs))
        return {"Subnets": self.subnets}

    def describe_route_tables(self, **kwargs):
        self.calls.append(("describe_route_tables", kwargs))
        return {"RouteTables": self.route_tables}


def zone(name, zone_id, state="available"):
    return {"ZoneName": name, "ZoneId": zone_id, "State": state}


def subnet(subnet_id, name, zone_id, tags=None, public_ip=False):
    return {"SubnetId": subnet_id, "AvailabilityZone": name, "AvailabilityZoneId": zone_id,
            "MapPublicIpOnLaunch": public_ip, "Tags": [{"Key": k, "Value": v} for k, v in (tags or {}).items()]}


def table(*subnet_ids, main=False, nat=False, igw=False, tgw=False):
    routes = [{"DestinationCidrBlock": "10.0.0.0/16", "GatewayId": "local"}]
    if nat:
        routes.append({"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": "nat-1"})
    if igw:
        routes.append({"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": "igw-1"})
    if tgw:
        routes.append({"DestinationCidrBlock": "0.0.0.0/0", "TransitGatewayId": "tgw-1"})
    associations = [{"SubnetId": s} for s in subnet_ids] + ([{"Main": True}] if main else [])
    return {"Associations": associations, "Routes": routes}


SEOUL = [zone("ap-northeast-2a", "apne2-az1"), zone("ap-northeast-2b", "apne2-az2"),
         zone("ap-northeast-2c", "apne2-az3"), zone("ap-northeast-2d", "apne2-az4")]


class ManagedVpcTests(unittest.TestCase):
    def test_the_first_three_available_zones_pass_when_supported(self):
        # The managed VPC never uses ap-northeast-2d (apne2-az4), which AgentCore doesn't support.
        ec2 = FakeEc2(zones=SEOUL)
        self.assertEqual(check_runtime_zones(ec2, "ap-northeast-2", ""), ["apne2-az1", "apne2-az2", "apne2-az3"])
        self.assertEqual([name for name, _ in ec2.calls], ["describe_availability_zones"])

    def test_skips_unavailable_zones_as_cdk_does(self):
        zones = [SEOUL[0], zone("ap-northeast-2b", "apne2-az2", state="impaired"), SEOUL[2], SEOUL[3]]
        with self.assertRaises(SystemExit) as failure:
            check_runtime_zones(FakeEc2(zones=zones), "ap-northeast-2", "")
        self.assertIn("ap-northeast-2d (apne2-az4)", str(failure.exception))

    def test_fails_naming_the_unsupported_zone_id(self):
        zones = [zone("us-east-1a", "use1-az4"), zone("us-east-1b", "use1-az3"), zone("us-east-1c", "use1-az1")]
        with self.assertRaises(SystemExit) as failure:
            check_runtime_zones(FakeEc2(zones=zones), "us-east-1", "")
        message = str(failure.exception)
        self.assertIn("us-east-1b (use1-az3)", message)
        self.assertNotIn("us-east-1a", message)
        self.assertIn("set vpc.vpcId", message)

    def test_a_v2_only_zone_fails_the_v1_runtime(self):
        zones = [zone("us-east-1a", "use1-az1"), zone("us-east-1b", "use1-az2"), zone("us-east-1c", "use1-az6")]
        with self.assertRaises(SystemExit) as failure:
            check_runtime_zones(FakeEc2(zones=zones), "us-east-1", "")
        self.assertIn("use1-az6 is supported only on platform version V2", str(failure.exception))

    def test_a_region_without_vpc_support_fails(self):
        with self.assertRaises(SystemExit) as failure:
            check_runtime_zones(FakeEc2(zones=SEOUL), "ap-east-1", "")
        self.assertIn("ap-east-1", str(failure.exception))


class ExistingVpcTests(unittest.TestCase):
    def test_checks_only_the_private_subnets_the_runtime_uses(self):
        ec2 = FakeEc2(
            subnets=[
                subnet("subnet-public", "ap-northeast-2d", "apne2-az4"),
                subnet("subnet-isolated", "ap-northeast-2d", "apne2-az4"),
                subnet("subnet-private-a", "ap-northeast-2a", "apne2-az1"),
                subnet("subnet-private-b", "ap-northeast-2b", "apne2-az2"),
            ],
            route_tables=[table("subnet-public", igw=True), table("subnet-isolated"),
                          table("subnet-private-a", "subnet-private-b", nat=True)],
        )
        self.assertEqual(check_runtime_zones(ec2, "ap-northeast-2", "vpc-1"), ["apne2-az1", "apne2-az2"])
        vpc_filter = {"Filters": [{"Name": "vpc-id", "Values": ["vpc-1"]}]}
        self.assertEqual(ec2.calls, [("describe_subnets", vpc_filter), ("describe_route_tables", vpc_filter)])

    def test_fails_naming_the_subnet_and_its_zone_id(self):
        ec2 = FakeEc2(
            subnets=[subnet("subnet-a", "ap-northeast-2a", "apne2-az1"), subnet("subnet-d", "ap-northeast-2d", "apne2-az4")],
            route_tables=[table("subnet-a", "subnet-d", nat=True)],
        )
        with self.assertRaises(SystemExit) as failure:
            check_runtime_zones(ec2, "ap-northeast-2", "vpc-1")
        message = str(failure.exception)
        self.assertIn("subnet-d in ap-northeast-2d (apne2-az4)", message)
        self.assertNotIn("subnet-a", message)
        self.assertIn("every private subnet with egress in vpc-1", message)

    def test_types_subnets_as_cdk_lookup_does(self):
        ec2 = FakeEc2(
            subnets=[
                # The aws-cdk:subnet-type tag wins over the routes.
                subnet("subnet-tagged-public", "ap-northeast-2d", "apne2-az4", tags={"aws-cdk:subnet-type": "Public"}),
                subnet("subnet-tagged-private", "ap-northeast-2a", "apne2-az1", tags={"aws-cdk:subnet-type": "Private"}),
                # Public IPs on launch make a subnet public.
                subnet("subnet-public-ip", "ap-northeast-2d", "apne2-az4", public_ip=True),
                # An unassociated subnet uses the main route table; a transit gateway default route is egress.
                subnet("subnet-main", "ap-northeast-2b", "apne2-az2"),
                subnet("subnet-tgw", "ap-northeast-2c", "apne2-az3"),
            ],
            route_tables=[table("subnet-tagged-public", "subnet-tagged-private", "subnet-public-ip", nat=True),
                          table(main=True, nat=True), table("subnet-tgw", tgw=True)],
        )
        self.assertEqual(check_runtime_zones(ec2, "ap-northeast-2", "vpc-1"), ["apne2-az1", "apne2-az2", "apne2-az3"])

    def test_a_vpc_without_private_subnets_fails(self):
        ec2 = FakeEc2(subnets=[subnet("subnet-public", "ap-northeast-2a", "apne2-az1")],
                      route_tables=[table("subnet-public", igw=True)])
        with self.assertRaises(SystemExit) as failure:
            check_runtime_zones(ec2, "ap-northeast-2", "vpc-1")
        self.assertIn("vpc-1 has no private subnets", str(failure.exception))


class TableTests(unittest.TestCase):
    def test_every_entry_is_a_zone_id_of_its_region(self):
        self.assertLessEqual(set(AGENTCORE_VPC_ZONES_V2_ONLY), set(AGENTCORE_VPC_ZONES))
        for region, zones in AGENTCORE_VPC_ZONES.items():
            zones = zones + AGENTCORE_VPC_ZONES_V2_ONLY.get(region, ())
            with self.subTest(region=region):
                prefixes = {re.fullmatch(r"([a-z0-9]+)-az\d+", zone_id).group(1) for zone_id in zones}
                self.assertEqual(len(prefixes), 1)
                # A V2-only zone isn't also listed as supported on V1.
                self.assertEqual(len(set(zones)), len(zones))

    def test_seoul_supports_az1_to_az3(self):
        self.assertEqual(AGENTCORE_VPC_ZONES["ap-northeast-2"], ("apne2-az1", "apne2-az2", "apne2-az3"))


if __name__ == "__main__":
    unittest.main()


class FakeBedrock:
    def __init__(self, status="ACTIVE", authorization="AUTHORIZED", region="AVAILABLE"):
        self.status, self.authorization, self.region, self.calls = status, authorization, region, []

    def get_inference_profile(self, inferenceProfileIdentifier):
        self.calls.append(("get_inference_profile", inferenceProfileIdentifier))
        return {"status": self.status, "models": []}

    def get_foundation_model_availability(self, modelId):
        self.calls.append(("get_foundation_model_availability", modelId))
        return {"authorizationStatus": self.authorization, "regionAvailability": self.region}


class FakeOpenSearch:
    def __init__(self, packages):
        self.packages, self.filters = packages, None

    def describe_packages(self, Filters):
        self.filters = Filters
        return {"PackageDetailsList": self.packages}


class TestModels(unittest.TestCase):
    def test_a_profile_is_checked_with_the_model_behind_it(self):
        bedrock = FakeBedrock()
        self.assertEqual(check_model(bedrock, "global.cohere.embed-v4:0"), "cohere.embed-v4:0")
        self.assertEqual(bedrock.calls, [("get_inference_profile", "global.cohere.embed-v4:0"),
                                         ("get_foundation_model_availability", "cohere.embed-v4:0")])

    def test_an_inactive_profile_or_an_unauthorized_model_fails_naming_it(self):
        with self.assertRaisesRegex(SystemExit, "global.cohere.embed-v4:0 is not ACTIVE"):
            check_model(FakeBedrock(status="INACTIVE"), "global.cohere.embed-v4:0")
        with self.assertRaisesRegex(SystemExit, "not ready for cohere.embed-v4:0"):
            check_model(FakeBedrock(authorization="NOT_AUTHORIZED"), "global.cohere.embed-v4:0")


class TestNori(unittest.TestCase):
    def test_finds_the_package_for_the_deployed_engine(self):
        opensearch = FakeOpenSearch([{"PackageID": "G26022645", "PackageStatus": "AVAILABLE"}])
        self.assertEqual(check_nori(opensearch), "G26022645")
        self.assertIn({"Name": "EngineVersion", "Value": ["OpenSearch_3.5"]}, opensearch.filters)
        self.assertIn({"Name": "PackageName", "Value": ["analysis-nori"]}, opensearch.filters)

    def test_fails_when_the_region_offers_no_package(self):
        with self.assertRaisesRegex(SystemExit, 'analyzer = "standard"'):
            check_nori(FakeOpenSearch([]))


class FakeAcm:
    def __init__(self, status, kind="AMAZON_ISSUED"):
        self.certificate, self.calls = {"Status": status, "Type": kind}, []

    def describe_certificate(self, **kwargs):
        self.calls.append(kwargs)
        return {"Certificate": self.certificate}


ARN = "arn:aws:acm:ap-northeast-2:123456789012:certificate/0f0e0d0c-0b0a-0908-0706-050403020100"


class TestCertificate(unittest.TestCase):
    def test_an_issued_certificate_in_the_region_passes(self):
        for kind in ("AMAZON_ISSUED", "IMPORTED"):
            acm = FakeAcm("ISSUED", kind)
            self.assertEqual(check_certificate(acm, ARN, "ap-northeast-2"), kind)
            self.assertEqual(acm.calls, [{"CertificateArn": ARN}])

    def test_an_unissued_certificate_fails_naming_its_status(self):
        # The load balancer would refuse it only at the end of the deploy, with a misleading error.
        for status in ("PENDING_VALIDATION", "FAILED", "EXPIRED"):
            with self.assertRaisesRegex(SystemExit, f"api.certificateArn is {status}, not ISSUED"):
                check_certificate(FakeAcm(status), ARN, "ap-northeast-2")

    def test_a_certificate_in_another_region_fails_without_a_call(self):
        acm = FakeAcm("ISSUED")
        with self.assertRaisesRegex(SystemExit, "in us-west-2, the stage's Region"):
            check_certificate(acm, ARN, "us-west-2")
        self.assertEqual(acm.calls, [])
