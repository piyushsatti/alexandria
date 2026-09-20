"""OAuth bearer-token verification for Alexandria's remote HTTP transport."""

from __future__ import annotations

import logging
from collections.abc import Iterable

LOGGER = logging.getLogger(__name__)


def _normalized_scopes(value: object) -> list[str]:
    if isinstance(value, str):
        return [scope for scope in value.split() if scope]
    if isinstance(value, list) and all(isinstance(scope, str) for scope in value):
        return list(value)
    return []


class LogtoJwtVerifier:
    """Verify Logto JWTs and restrict Alexandria to explicitly allowed owners."""

    def __init__(
        self,
        *,
        issuer: str,
        resource: str,
        jwks_url: str,
        required_scopes: Iterable[str],
        allowed_subjects: Iterable[str],
        algorithms: Iterable[str] = ("ES384",),
        jwks_client=None,
    ) -> None:
        import jwt

        self.issuer = issuer.rstrip("/")
        self.resource = resource.rstrip("/")
        self.required_scopes = frozenset(required_scopes)
        self.allowed_subjects = frozenset(allowed_subjects)
        self.algorithms = tuple(algorithms)
        if not self.required_scopes:
            raise ValueError("At least one required scope must be configured")
        if not self.allowed_subjects:
            raise ValueError("At least one allowed Logto subject must be configured")
        self.jwks_client = jwks_client or jwt.PyJWKClient(
            jwks_url,
            cache_keys=True,
            max_cached_keys=8,
            cache_jwk_set=True,
            lifespan=300,
            timeout=5,
            cooldown_duration=30,
        )

    async def verify_token(self, token: str):
        import anyio
        import jwt
        from mcp.server.auth.provider import AccessToken

        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in self.algorithms:
                return None
            signing_key = await anyio.to_thread.run_sync(
                self.jwks_client.get_signing_key_from_jwt, token
            )
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=self.algorithms,
                issuer=self.issuer,
                audience=[self.resource, f"{self.resource}/"],
                options={"require": ["iss", "sub", "aud", "exp"]},
            )
        except (jwt.PyJWTError, OSError, ValueError):
            LOGGER.warning("Rejected an invalid Alexandria bearer token")
            return None

        subject = claims.get("sub")
        scopes = _normalized_scopes(claims.get("scope"))
        if subject not in self.allowed_subjects:
            LOGGER.warning("Rejected a bearer token for an unauthorized subject")
            return None
        if not self.required_scopes.issubset(scopes):
            LOGGER.warning("Rejected a bearer token without the required scope")
            return None

        client_id = claims.get("client_id") or claims.get("azp")
        if not isinstance(client_id, str) or not client_id:
            LOGGER.warning("Rejected a bearer token without a client identity")
            return None
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.resource,
            subject=subject,
            claims=claims,
        )
