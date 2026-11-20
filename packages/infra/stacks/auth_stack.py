"""Cognito user pool backing the CUSTOM_JWT authorizers.

The Runtime validates the shopper's token; the Gateway validates the agent's own
token, which AgentCore Identity mints with the agent client below. One pool issues
both. Caller identity is taken from the token's `sub` claim, never from the request
body.

The agent's Gateway token comes from an on-behalf-of exchange (RFC 8693 delegation):
Cognito issues a token for the `aria-tools` resource server whose `sub` is the
shopper and whose `act.sub` is the agent client. Exchange needs the Essentials or
Plus feature plan and a managed-login domain, and it can only narrow scopes, so the
web client also requests `aria-tools/invoke`.
"""

import aws_cdk as cdk
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_cognito as cognito
from cdk_nag import NagSuppressions
from constructs import Construct
from config.loader import GATEWAY_TOKEN_AUDIENCE, GATEWAY_TOKEN_SCOPE, AuthConfig


class AuthStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, *, settings: AuthConfig | None = None, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        settings = settings or AuthConfig()
        ns: str = self.node.try_get_context("ns")
        is_prod = bool(self.node.try_get_context("isProd"))

        self.user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=f"{ns.lower()}-users",
            self_sign_up_enabled=False,
            mfa={"off": cognito.Mfa.OFF, "optional": cognito.Mfa.OPTIONAL, "required": cognito.Mfa.REQUIRED}[settings.mfa],
            mfa_second_factor=cognito.MfaSecondFactor(sms=False, otp=True),
            feature_plan=cognito.FeaturePlan.PLUS if is_prod else cognito.FeaturePlan.ESSENTIALS,
            deletion_protection=is_prod,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            # Recovery codes go only to the verified sign-in email, never by SMS.
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            password_policy=cognito.PasswordPolicy(
                min_length=12,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            # Threat protection (compromised-credential detection, adaptive auth) requires the
            # PLUS feature plan, so Prod sets both; Dev runs ESSENTIALS with no enforcement.
            standard_threat_protection_mode=cognito.StandardThreatProtectionMode.FULL_FUNCTION if is_prod else cognito.StandardThreatProtectionMode.NO_ENFORCEMENT,
            removal_policy=cdk.RemovalPolicy.RETAIN if is_prod else cdk.RemovalPolicy.DESTROY,
        )

        # Managed login (not the classic hosted UI): Cognito's token exchange requires it.
        # auth.domainPrefix keeps the account ID out of the public login URL; empty uses <namespace>-<account ID>.
        domain = self.user_pool.add_domain(
            "LoginDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=settings.domain_prefix or f"{ns.lower()}-{self.account}"),
            managed_login_version=cognito.ManagedLoginVersion.NEWER_MANAGED_LOGIN,
        )
        scope = cognito.ResourceServerScope(scope_name="chat", scope_description="Use the shopping assistant")
        resource_server = self.user_pool.add_resource_server("ApiScopes", identifier="aria", scopes=[scope])
        tools_scope = cognito.ResourceServerScope(scope_name="invoke", scope_description="Call the shopping assistant's Gateway tools")
        tools_server = self.user_pool.add_resource_server("ToolScopes", identifier=GATEWAY_TOKEN_AUDIENCE, scopes=[tools_scope])
        # Public OAuth client; the browser library uses authorization code + PKCE.
        self.user_pool_client = self.user_pool.add_client(
            "WebClient",
            user_pool_client_name=f"{ns.lower()}-web",
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                # aria-tools/invoke lets the agent exchange this token for a Gateway token;
                # the Gateway itself never accepts a token issued to this client.
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.PROFILE, cognito.OAuthScope.EMAIL,
                        cognito.OAuthScope.resource_server(resource_server, scope), cognito.OAuthScope.resource_server(tools_server, tools_scope)],
                callback_urls=settings.callback_urls, logout_urls=settings.logout_urls,
            ),
            generate_secret=False,
            access_token_validity=cdk.Duration.hours(1),
            id_token_validity=cdk.Duration.hours(1),
            refresh_token_validity=cdk.Duration.days(30),
            prevent_user_existence_errors=True,
        )

        self.discovery_url = (
            f"https://cognito-idp.{self.region}.amazonaws.com/"
            f"{self.user_pool.user_pool_id}/.well-known/openid-configuration"
        )
        # Managed login renders sign-in pages only for app clients with a style.
        cognito.CfnManagedLoginBranding(
            self, "WebLoginBranding", user_pool_id=self.user_pool.user_pool_id,
            client_id=self.user_pool_client.user_pool_client_id, use_cognito_provided_values=True,
        ).node.add_dependency(domain)

        # The agent's confidential client. AgentCore Identity uses it for the client-credentials
        # actor token and to perform the delegation exchange.
        self.agent_client = self.user_pool.add_client(
            "AgentClient",
            user_pool_client_name=f"{ns.lower()}-agent",
            generate_secret=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[cognito.OAuthScope.resource_server(tools_server, tools_scope)],
            ),
            access_token_validity=cdk.Duration.hours(1),
            prevent_user_existence_errors=True,
        )
        # The L2 AuthFlow has no token-exchange flows yet, so set them on the resource.
        self.agent_client.node.default_child.explicit_auth_flows = ["ALLOW_TOKEN_EXCHANGE_DELEGATION", "ALLOW_REFRESH_TOKEN_AUTH"]

        # Lives here, beside the client: its secret is read at deploy time and must not
        # cross stacks, where it would become a visible CloudFormation export.
        self.gateway_credential_provider_name = f"{ns.lower()}-gateway"
        provider_config = agentcore.CfnOAuth2CredentialProvider
        self.gateway_credential_provider = agentcore.CfnOAuth2CredentialProvider(
            self,
            "GatewayCredentialProvider",
            name=self.gateway_credential_provider_name,
            credential_provider_vendor="CustomOauth2",
            oauth2_provider_config_input=provider_config.Oauth2ProviderConfigInputProperty(
                custom_oauth2_provider_config=provider_config.CustomOauth2ProviderConfigInputProperty(
                    oauth_discovery=provider_config.Oauth2DiscoveryProperty(discovery_url=self.discovery_url),
                    client_id=self.agent_client.user_pool_client_id,
                    client_secret=self.agent_client.user_pool_client_secret.unsafe_unwrap(),
                    on_behalf_of_token_exchange_config=provider_config.OnBehalfOfTokenExchangeConfigProperty(
                        grant_type="TOKEN_EXCHANGE",
                        token_exchange_grant_type_config=provider_config.TokenExchangeGrantTypeConfigProperty(  # nosec B106 # how AgentCore Identity obtains the actor token, not a credential
                            actor_token_content="M2M", actor_token_scopes=[GATEWAY_TOKEN_SCOPE],  # nosec B106 # how AgentCore Identity obtains the actor token, not a credential
                        ),
                    ),
                )
            ),
        )
        self.gateway_credential_provider.node.add_dependency(domain)
        # user_pool_client_secret reads the generated secret through CDK's AwsCustomResource,
        # whose singleton function exists only after that property is used above.
        NagSuppressions.add_resource_suppressions_by_path(
            self,
            f"/{self.stack_name}/AWS679f53fac002430cb0da5b7982bd2287/ServiceRole/Resource",
            [{
                "id": "AwsSolutions-IAM4",
                "reason": "CDK's AwsCustomResource reads the agent client's generated secret once at deploy time; its singleton function uses the AWS-managed basic execution policy",
                # cdk-nag reads appliesTo; an applies_to key would silently suppress every IAM4 finding here.
                "appliesTo": ["Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"],
            }],
        )

        if settings.mfa != "required":
            NagSuppressions.add_resource_suppressions(self.user_pool, [{
                "id": "AwsSolutions-COG2",
                "reason": f"auth.mfa = {settings.mfa}: shoppers aren't made to register an authenticator; auth.mfa = required makes them",
            }])
        if not is_prod:
            NagSuppressions.add_resource_suppressions(self.user_pool, [{
                "id": "AwsSolutions-COG8",
                "reason": "Dev uses the Essentials feature plan; Prod uses Plus with threat protection",
            }])

        cdk.CfnOutput(self, "UserPoolId", value=self.user_pool.user_pool_id)
        cdk.CfnOutput(self, "UserPoolClientId", value=self.user_pool_client.user_pool_client_id)
        cdk.CfnOutput(self, "DiscoveryUrl", value=self.discovery_url)
        cdk.CfnOutput(self, "AgentClientId", value=self.agent_client.user_pool_client_id)
        cdk.CfnOutput(self, "GatewayCredentialProviderName", value=self.gateway_credential_provider_name)
