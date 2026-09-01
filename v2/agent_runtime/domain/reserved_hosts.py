"""Subdomain labels the product keeps for itself — ONE set, two enforcement points.

Under a wildcard app-host suffix (config.app_host_suffix), `<label>.<domain>` serves the agent
whose id is the label, and a published bundle id IS therefore a public hostname. That collapses
two namespaces into one, and the collapse has a trap: whoever publishes a bundle called "api"
or "admin" would own a hostname every user reads as the PLATFORM's. bundle_owners makes claims
first-come-forever (a conditional put, deliberately), so the mistake could never be undone by
policy later — it has to be refused at the door.

Two readers, and they must agree, which is why this is a module and not two lists:

  publish intake   refuses a submission whose bundle id is reserved (the claim never happens)
  gateway          never derives a reserved label from a Host header (so even an id that
                   predates this list, or arrives by a path other than our intake, serves nothing)

Names come in two kinds: surfaces this deployment actually routes (admin, platform, marketplace,
www — see infra/modules/dns.tf) and conventional infrastructure labels (api, mail, status…)
that users assume belong to the operator of the domain. Adding a name here is one line; removing
one is forever-after unsafe if anything shipped while it was claimable.
"""

from __future__ import annotations

RESERVED_HOST_LABELS: frozenset[str] = frozenset(
    {
        # surfaces the deployment routes today (dns.tf / nginx server_name / ALB rules)
        "www",
        "admin",
        "platform",
        "marketplace",
        # the platform's own services — these answer on the apex's ports, and a bundle by one
        # of these names would read as the service itself
        "accounts",
        "api",
        "ingest",
        "registry",
        "publish",
        "builder",
        "daemon",
        "gateway",
        "model-proxy",
        # conventional operator-owned labels people trust implicitly
        "mail",
        "smtp",
        "imap",
        "ftp",
        "ns",
        "ns1",
        "ns2",
        "status",
        "docs",
        "blog",
        "help",
        "support",
        "billing",
        "login",
        "auth",
        "sso",
        "app",
        "cdn",
        "static",
        "assets",
        "dev",
        "staging",
        "test",
    }
)


def is_reserved_host_label(label: str) -> bool:
    """Case-insensitive membership — Host headers and bundle ids both arrive in the wild."""
    return (label or "").strip().lower() in RESERVED_HOST_LABELS
