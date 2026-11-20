"""One issuer contract shared by API, Runtime and Gateway."""
from dataclasses import dataclass
from config.loader import Config


@dataclass(frozen=True)
class Identity:
    provider: str
    issuer: str
    client_id: str
    audiences: list[str]
    clients: list[str]
    scopes: list[str]
    tenant_claim: str

    @classmethod
    def resolve(cls, config: Config, discovery_url: str = "", client_id: str = ""):
        auth = config.auth
        if auth.provider == "cognito":
            if not discovery_url or not client_id:
                raise ValueError("Managed Cognito outputs are required")
            return cls("cognito", discovery_url.removesuffix("/.well-known/openid-configuration"), client_id, [], [client_id], auth.scopes, auth.tenant_claim)
        return cls("oidc", auth.issuer, auth.client_id, auth.audiences, auth.allowed_clients, auth.scopes, auth.tenant_claim)

    def authorizer(self):
        discovery_url = self.issuer.rstrip("/") + "/.well-known/openid-configuration"
        return dict(discovery_url=discovery_url, allowed_audience=self.audiences or None, allowed_clients=self.clients or None, allowed_scopes=self.scopes)

    def environment(self):
        return {"AUTH_PROVIDER": self.provider, "OIDC_ISSUER": self.issuer, "OIDC_CLIENT_ID": self.client_id, "OIDC_AUDIENCES": ",".join(self.audiences), "OIDC_ALLOWED_CLIENTS": ",".join(self.clients), "OIDC_SCOPES": " ".join(self.scopes), "OIDC_TENANT_CLAIM": self.tenant_claim}
