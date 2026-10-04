# Model submission identity configuration

The public intake and submitter status routes accept short-lived bearer tokens resolved to trusted
account claims. Deployments set `PCB_API_IDENTITY_FILE` to a secret-mounted JSON file; no raw token
is stored in this file or in the submission database. The identity issuer hashes each token with
SHA-256 and provisions its verified email, roles, MFA state, and expiration. Token rotation is
performed by replacing the mounted file. Staging and production API startup fails closed when the
file is absent or malformed.

The file format is strict and rejects unknown fields:

```json
{
  "schema_version": 1,
  "principals": [
    {
      "token_sha256": "<64 lowercase hexadecimal characters>",
      "principal": {
        "subject_id": "stable-account-subject",
        "roles": ["submitter"],
        "mfa": false,
        "email": "verified@example.org",
        "email_verified": true,
        "expires_at": 1800000000
      }
    }
  ]
}
```

Only the issuer may write this file. Deploy it through the existing secret/config management path,
restrict read access to the API process, and use account tokens with short expiry. The development
browser suite creates a temporary identity file with synthetic accounts. It does not provision
provider secrets or enable model calls. Production OAuth/OIDC login and token issuance, plus
provisioning the referenced provider secrets, remain deployment inputs for Prompt 33. When
`PCB_DATABASE_URL` is set, the API automatically wires the durable PostgreSQL submission, endpoint,
and run repositories. The API permission checks, token-claim validation, endpoint approval service,
and bounded run service are enforced server-side.
