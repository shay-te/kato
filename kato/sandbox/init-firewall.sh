#!/bin/bash
# Egress firewall — strict allowlist mode.
#
# Default-DROP iptables policy. The only outbound destinations the
# container can reach are:
#   - api.anthropic.com  (Claude must talk to its model — non-negotiable)
#   - DNS over UDP/53    (needed to resolve api.anthropic.com)
#   - loopback           (intra-container)
#
# Everything else — github, npm, statsig, sentry, pastebins, exfil
# endpoints — is rejected at the kernel. This stays in force even if
# Claude is launched with --permission-mode bypassPermissions because
# bypass lives in userspace; iptables lives in the kernel.

set -euo pipefail
IFS=$'\n\t'

echo "[kato-sandbox] applying egress allowlist (api.anthropic.com only)"

# Wipe anything inherited.
iptables -F
iptables -X
iptables -t nat -F   2>/dev/null || true
iptables -t nat -X   2>/dev/null || true
iptables -t mangle -F 2>/dev/null || true
iptables -t mangle -X 2>/dev/null || true
ipset destroy allowed-domains 2>/dev/null || true

# Defense-in-depth: also lock down IPv6 in case the kernel sysctl
# ``net.ipv6.conf.all.disable_ipv6=1`` is somehow ignored (older
# kernels, weird networking stacks). If ip6tables isn't available we
# accept it — IPv6 is already disabled at the sysctl level.
if command -v ip6tables >/dev/null 2>&1; then
    ip6tables -F  2>/dev/null || true
    ip6tables -X  2>/dev/null || true
    ip6tables -P INPUT   DROP 2>/dev/null || true
    ip6tables -P FORWARD DROP 2>/dev/null || true
    ip6tables -P OUTPUT  DROP 2>/dev/null || true
fi

# Loopback freely.
iptables -A INPUT  -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT

# DNS — only to the pinned resolvers (matches the ``--dns`` flag in
# ``wrap_command``). Allowing arbitrary UDP/53 would let Claude
# exfiltrate by encoding data into queries to an attacker-controlled
# resolver. By restricting to 1.1.1.1 / 1.0.0.1 the worst case is
# Cloudflare-logged DNS lookups for nonsense names.
iptables -A OUTPUT -p udp --dport 53 -d 1.1.1.1/32 -j ACCEPT
iptables -A OUTPUT -p udp --dport 53 -d 1.0.0.1/32 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -d 1.1.1.1/32 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -d 1.0.0.1/32 -j ACCEPT

# ICMP entirely off — no ping, no traceroute, no ICMP-tunnel exfil.
iptables -A OUTPUT -p icmp -j DROP
iptables -A INPUT  -p icmp -j DROP

# Established responses for traffic we initiated. Order matters:
# this comes AFTER the restrictive rules above so a stray inbound
# DNS from an unauthorized server can't piggy-back on conntrack.
iptables -A INPUT  -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# Resolve allowlisted hosts into an ipset so the iptables rule can
# match them by destination IP.
ipset create allowed-domains hash:ip family inet -exist

resolve_into_set() {
    local domain="$1"
    local ips
    ips=$(getent ahostsv4 "$domain" | awk '{print $1}' | sort -u)
    if [ -z "$ips" ]; then
        echo "[kato-sandbox] ERROR: failed to resolve $domain — DNS broken?" >&2
        exit 1
    fi
    while IFS= read -r ip; do
        if [[ ! "$ip" =~ ^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$ ]]; then
            echo "[kato-sandbox] ERROR: invalid IP for $domain: $ip" >&2
            exit 1
        fi
        ipset add allowed-domains "$ip" -exist
    done <<<"$ips"
}

resolve_into_set "api.anthropic.com"

# Allow only HTTPS to the resolved Anthropic endpoints.
iptables -A OUTPUT -m set --match-set allowed-domains dst \
         -p tcp --dport 443 -j ACCEPT

# Default policy — drop everything not explicitly allowed.
iptables -P INPUT   DROP
iptables -P FORWARD DROP
iptables -P OUTPUT  DROP

# Explicit REJECT at the end of OUTPUT so denied connections fail
# fast (ICMP admin-prohibited) instead of timing out.
iptables -A OUTPUT -j REJECT --reject-with icmp-admin-prohibited

echo "[kato-sandbox] firewall up. Allowed destinations:"
ipset list allowed-domains | tail -n +8 | sed 's/^/[kato-sandbox]   /'

# Sanity: api.anthropic.com reachable, example.com is not.
if ! curl --connect-timeout 5 -sI https://api.anthropic.com/ >/dev/null 2>&1; then
    echo "[kato-sandbox] WARNING: cannot reach api.anthropic.com — Claude will not work" >&2
fi
if curl --connect-timeout 3 -sI https://example.com/ >/dev/null 2>&1; then
    echo "[kato-sandbox] ERROR: example.com reachable — firewall did not apply" >&2
    exit 1
fi

echo "[kato-sandbox] firewall verified"

# Final sanity: confirm the OUTPUT chain has the expected default policy
# AND a REJECT catchall. If either is missing the firewall didn't take
# effect for some reason and we should not let Claude run.
if ! iptables -L OUTPUT -n | head -n 1 | grep -q '(policy DROP)'; then
    echo "[kato-sandbox] ERROR: OUTPUT default policy is not DROP — refusing to start" >&2
    exit 1
fi
if ! iptables -S OUTPUT | grep -q '\-j REJECT '; then
    echo "[kato-sandbox] ERROR: OUTPUT REJECT catchall missing — refusing to start" >&2
    exit 1
fi
echo "[kato-sandbox] policy + catchall confirmed"
