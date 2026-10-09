"""Point an already-prepared local stack at a public HTTPS domain (Oracle POC).

Run on the VM after ``scripts/local_stack.py prepare``. It rewrites, in place:

- ``.env``: OIDC issuer, redirect URI and web origin to ``https://<domain>``;
  Keycloak is served under ``/kc`` because the web app owns ``/auth/callback``.
- ``.cache/keycloak-import/polycodebench-local-realm.json``: client redirect URIs,
  web origins and post-logout URIs, and ``sslRequired=external``.
- ``infra/oracle/deploy/.deploy.env``: the domain and ACME e-mail for Caddy/compose.

No secret value is printed. Re-running with the same domain is a no-op.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"
REALM_PATH = ROOT / ".cache" / "keycloak-import" / "polycodebench-local-realm.json"
DEPLOY_ENV_PATH = ROOT / "infra" / "oracle" / "deploy" / ".deploy.env"
REALM = "polycodebench-local"
KEYCLOAK_PATH = "/kc"
DOMAIN_PATTERN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def _rewrite_env(text: str, updates: dict[str, str]) -> str:
    lines = text.splitlines()
    seen: set[str] = set()
    for index, line in enumerate(lines):
        name = line.split("=", 1)[0]
        if name in updates:
            lines[index] = f"{name}={updates[name]}"
            seen.add(name)
    lines.extend(f"{name}={value}" for name, value in updates.items() if name not in seen)
    return "\n".join(lines) + "\n"


def configure(domain: str, acme_email: str) -> None:
    domain = domain.strip().lower()
    if not DOMAIN_PATTERN.match(domain):
        raise SystemExit(f"not a valid DNS name: {domain!r}")
    if acme_email and "@" not in acme_email:
        raise SystemExit("the ACME contact e-mail is not an e-mail address")
    if not ENV_PATH.exists() or not REALM_PATH.exists():
        raise SystemExit("run `scripts/local_stack.py prepare` first")

    origin = f"https://{domain}"
    ENV_PATH.write_text(
        _rewrite_env(
            ENV_PATH.read_text(encoding="utf-8"),
            {
                "PCB_OIDC_ISSUER": f"{origin}{KEYCLOAK_PATH}/realms/{REALM}",
                "PCB_OIDC_REDIRECT_URI": f"{origin}/auth/callback",
                "PCB_WEB_ORIGIN": origin,
            },
        ),
        encoding="utf-8",
        newline="\n",
    )

    realm = json.loads(REALM_PATH.read_text(encoding="utf-8"))
    realm["sslRequired"] = "external"
    for client in realm["clients"]:
        if client.get("clientId") == "polycodebench-web":
            client["redirectUris"] = [f"{origin}/auth/callback"]
            client["webOrigins"] = [origin]
            client.setdefault("attributes", {})["post.logout.redirect.uris"] = f"{origin}/*"
    REALM_PATH.write_text(json.dumps(realm, indent=2) + "\n", encoding="utf-8")

    DEPLOY_ENV_PATH.write_text(
        f"PCB_PUBLIC_DOMAIN={domain}\nPCB_ACME_EMAIL={acme_email}\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Configured .env, Keycloak realm import and deploy env for {origin}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", required=True)
    parser.add_argument("--acme-email", default="", help="optional Let's Encrypt contact")
    args = parser.parse_args()
    configure(args.domain, args.acme_email)


if __name__ == "__main__":
    main()
