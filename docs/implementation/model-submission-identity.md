# Model submission identity configuration

The public submission page uses a server-side generic OpenID Connect authorization-code flow with
PKCE. Browser users are redirected to the configured identity provider; the app verifies the
provider's signed ID token, issuer, audience, nonce, expiry and `email_verified` claim. The verified
issuer and subject form the stable submitter identity. Provider access tokens are discarded and
never enter browser storage or the submission API.

Configure the web service with:

- `PCB_OIDC_ISSUER`: exact issuer identifier from the provider's discovery document.
- `PCB_OIDC_CLIENT_ID`: registered OIDC client identifier.
- `PCB_OIDC_CLIENT_SECRET`: optional confidential-client secret, held only by the web service.
- `PCB_OIDC_REDIRECT_URI`: exact registered callback URL ending in `/auth/callback`.
- `PCB_WEB_ORIGIN`: canonical public web origin, without a path.
- `PCB_WEB_AUTH_SIGNING_KEY`: a randomly generated secret of at least 32 bytes. Mount the same
  value into the web service and API service through secret management.

The web service discovers authorization, token and JWKS endpoints from the configured issuer. It
supports RS256 and ES256 ID-token signatures. The callback requires the state cookie, PKCE verifier
and nonce created for that login. Redirect destinations are restricted to same-origin relative
paths. Production URLs must use HTTPS. Local HTTP issuer and callback URLs are allowed only in the
development environment.

After verification, the web service sets an HttpOnly, Secure, SameSite=Lax, host-only session cookie
with an eight-hour lifetime. It creates a five-minute HS256 assertion for the API on each server-side
BFF request. The API accepts these assertions only with the exact `submitter` role, matching issuer
and audience, verified email, a valid expiry and the shared signing key. The public request and status
routes reject cross-origin mutations and never accept bearer tokens from browser input. The BFF
overwrites the request email with the signed-in account email before forwarding it.

`PCB_API_IDENTITY_FILE` remains required in staging and production for the existing reviewer,
administrator and publisher identity records. Its strict format stores SHA-256 fingerprints rather
than raw bearer values. The web-issued assertion cannot grant those privileged roles; it always
contains only `submitter`.

The Prompt 32 Playwright suite starts an isolated synthetic OIDC provider with a generated signing
key. It exercises discovery, authorization code, PKCE, callback validation, unverified-email
rejection, submitter ownership and the signed BFF-to-API assertion. These fixtures do not configure a
production identity provider, provision provider secrets, contact model endpoints, or enable model
calls. Production provider registration and secret mounting remain deployment configuration.
