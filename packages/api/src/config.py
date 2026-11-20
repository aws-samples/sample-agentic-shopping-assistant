"""API configuration."""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class ApiConfig:
    agent_runtime_arn: str
    memory_id: str
    region: str
    qualifier: str
    cors_allow_origins: list[str]

    auth_provider: str = "cognito"
    oidc_issuer: str = ""
    oidc_audiences: tuple[str, ...] = ()
    oidc_allowed_clients: tuple[str, ...] = ()
    oidc_scopes: tuple[str, ...] = ("aria/chat",)
    oidc_tenant_claim: str = ""
    request_timeout: int = 120

    def validate(self):
        if self.auth_provider not in ("cognito", "oidc") or not self.oidc_issuer.startswith("https://"):
            raise ValueError("Configure AUTH_PROVIDER and an HTTPS OIDC_ISSUER")
        if not (self.oidc_audiences or self.oidc_allowed_clients) or not self.oidc_scopes:
            raise ValueError("Configure token recipients and required API scopes")
        if not self.agent_runtime_arn.startswith("arn:") or not 10 <= self.request_timeout <= 300:
            raise ValueError("Configure AGENT_RUNTIME_ARN and a request timeout between 10 and 300 seconds")

    @classmethod
    def from_env(cls) -> "ApiConfig":
        return cls(
            auth_provider=os.environ.get("AUTH_PROVIDER", "cognito"),
            oidc_issuer=os.environ.get("OIDC_ISSUER", ""),
            oidc_audiences=tuple(filter(None, os.environ.get("OIDC_AUDIENCES", "").split(","))),
            oidc_allowed_clients=tuple(filter(None, os.environ.get("OIDC_ALLOWED_CLIENTS", "").split(","))),
            oidc_scopes=tuple(os.environ.get("OIDC_SCOPES", "aria/chat").split()),
            oidc_tenant_claim=os.environ.get("OIDC_TENANT_CLAIM", ""),
            request_timeout=int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "120")),
            agent_runtime_arn=os.environ.get("AGENT_RUNTIME_ARN", ""),
            # Short-term memory is always on in a deployment; empty only outside one (tests).
            memory_id=os.environ.get("AGENTCORE_MEMORY_ID", ""),
            region=os.environ.get("AWS_REGION", "ap-northeast-2"),
            qualifier=os.environ.get("AGENT_RUNTIME_QUALIFIER", "DEFAULT"),
            cors_allow_origins=[
                origin.strip()
                for origin in os.environ.get("CORS_ALLOW_ORIGINS", "http://localhost:4000").split(",")
                if origin.strip()
            ],
        )


@lru_cache(1)
def get_config() -> ApiConfig:
    return ApiConfig.from_env()
