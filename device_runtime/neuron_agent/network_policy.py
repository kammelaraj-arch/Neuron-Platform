"""Applies the parent-only inbound firewall described in
brain.json["network_policy"]. Run by install.sh on first boot AND
re-applied by the agent at every start (idempotent).

Hard contract from CLAUDE.md:
    inbound_policy = deny
    allow_loopback = true
    allow_established_outbound = true
    parent_only = true
    ssh_enabled_at_boot = false

Emits an nftables ruleset that:
    table inet neuron-policy {
        chain input { type filter hook input priority 0; policy drop;
            iif "lo" accept;
            ct state {established, related} accept;
        }
        chain forward { type filter hook forward priority 0; policy drop; }
        chain output { type filter hook output priority 0; policy accept; }
    }

That's it — every other inbound packet is dropped silently. The
established+related rule is what keeps the device's OUTBOUND mTLS
sessions to the parent working (without it, the parent's replies on
the same session also get dropped).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path


log = logging.getLogger("neuron_agent.network_policy")


NFT_RULESET = """\
flush ruleset
table inet neuron-policy {
    chain input {
        type filter hook input priority 0; policy drop;
        iif "lo" accept
        ct state { established, related } accept
        # ICMP echo / unreachable are useful for parent diagnostics.
        ip protocol icmp icmp type { echo-request, destination-unreachable } accept
        ip6 nexthdr ipv6-icmp accept
    }
    chain forward {
        type filter hook forward priority 0; policy drop;
    }
    chain output {
        type filter hook output priority 0; policy accept;
    }
}
"""


def apply(policy: dict | None, dry_run: bool = False) -> bool:
    """Render + apply the nftables ruleset implied by `policy`. Returns
    True on success, False on failure (logged). If `dry_run=True`, only
    writes /etc/nftables.d/neuron.nft without running `nft -f`."""
    if policy is None:
        log.warning("brain has no network_policy — skipping firewall")
        return False
    if policy.get("inbound_policy") != "deny":
        log.warning("inbound_policy=%r != 'deny' — refusing to apply non-deny ruleset",
                    policy.get("inbound_policy"))
        return False

    target = Path("/etc/nftables.d/neuron.nft")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(NFT_RULESET)
    except OSError as e:
        log.error("could not write %s: %s", target, e)
        return False

    if dry_run:
        log.info("dry-run — wrote %s, not invoking nft", target)
        return True

    if shutil.which("nft") is None:
        log.warning("nft binary not found — firewall not active (dev host?)")
        return False
    try:
        subprocess.run(["nft", "-f", str(target)], check=True,
                       capture_output=True, timeout=10)
        log.info("applied nftables ruleset from %s", target)
        return True
    except subprocess.CalledProcessError as e:
        log.error("nft -f failed: rc=%s stderr=%s",
                  e.returncode, e.stderr.decode("utf-8", "replace")[:300])
        return False
    except subprocess.TimeoutExpired:
        log.error("nft -f timed out")
        return False
