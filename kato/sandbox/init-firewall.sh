#!/bin/bash
# Egress firewall — strict allowlist mode.
#
# Default-DROP iptables policy. The only outbound destinations the
# container can reach are:
#   - api.anthropic.com  (Claude must talk to its model — non-negotiable)
#   - DNS over UDP/53    (needed to resolve api.anthropic.com — rate-limited)
#   - loopback           (intra-container)
#
# Everything else — github, npm, statsig, sentry, pastebins, exfil
# endpoints — is rejected at the kernel. This stays in force even if
# Claude is launched with --permission-mode bypassPermissions because
# bypass lives in userspace; iptables lives in the kernel.
#
# Notable defenses-in-depth applied below:
#
#   * **DNS rate limit** via ``-m hashlimit``. Even though DNS is
#     restricted to Cloudflare's resolvers (1.1.1.1 / 1.0.0.1), those
#     are *recursive* — a query for ``<encoded>.attacker.com`` is
#     forwarded to whatever authoritative server owns ``attacker.com``,
#     so an attacker who controls any nameserver can read the encoded
#     subdomain. Capping queries to ~60/min bounds the bandwidth of
#     this side channel from "ship the whole repo in seconds" to
#     "trickle for hours and hope no one notices."
#
#   * **Explicit RFC1918 / link-local / multicast denies** before the
#     allowlist ACCEPT. Defense-in-depth: if the api.anthropic.com
#     ipset ever resolved to a private IP (DNS poisoning, malformed
#     response), the connection would still be denied. Also blocks the
#     well-known cloud metadata service at 169.254.169.254 (AWS IMDS,
#     GCP metadata) so the sandbox cannot exfil instance credentials
#     when run on a cloud VM.
#
#   * **Anthropic reachability check is fail-closed.** If the only
#     allowed destination cannot be reached at firewall-init time,
#     we exit 1 rather than continuing — Claude can't function
#     without it anyway, and a "we tried but couldn't" warning is
#     too easy to miss in noisy logs.

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

# ----- Explicit denies before any ACCEPT -----
#
# These come BEFORE the allowlist so that even if some downstream rule
# (or a future maintainer's edit) tried to ACCEPT one of these, it
# would already have been DROPPED. The order matters:
#
#   1. Cloud-metadata IP (most important — credentials live behind it)
#   2. RFC1918 private ranges (host-bridge IPs, dev services on LAN)
#   3. Link-local + multicast + broadcast
#
# We use ``DROP`` rather than ``REJECT`` here so a probe gets a
# silent timeout rather than a fast "admin-prohibited" — prevents
# the sandboxed process from quickly enumerating which addresses
# are denied vs unreachable.
iptables -A OUTPUT -d 169.254.169.254/32 -j DROP   # AWS IMDS / GCP metadata
iptables -A OUTPUT -d 169.254.0.0/16    -j DROP    # link-local (incl. APIPA)
iptables -A OUTPUT -d 10.0.0.0/8        -j DROP    # RFC1918
iptables -A OUTPUT -d 172.16.0.0/12     -j DROP    # RFC1918 (incl. docker0)
iptables -A OUTPUT -d 192.168.0.0/16    -j DROP    # RFC1918
iptables -A OUTPUT -d 100.64.0.0/10     -j DROP    # CGNAT
iptables -A OUTPUT -d 224.0.0.0/4       -j DROP    # multicast
iptables -A OUTPUT -d 240.0.0.0/4       -j DROP    # reserved
iptables -A OUTPUT -d 255.255.255.255/32 -j DROP   # limited broadcast

# DNS — only to the pinned recursive resolvers (matches the ``--dns``
# flag in ``wrap_command``).
#
# IMPORTANT: 1.1.1.1 / 1.0.0.1 are *recursive* resolvers, not endpoints.
# A query for ``<encoded-data>.attacker.com`` will be forwarded to
# attacker.com's authoritative nameserver, which logs the subdomain.
# That's a viable exfiltration channel; the rate limit below caps the
# bandwidth. Allowing arbitrary UDP/53 (any resolver) would be much
# worse — an attacker-controlled resolver could be queried directly.
#
# Rate limit: 60 queries / minute with a burst of 20. Normal Claude
# operation needs maybe a dozen DNS lookups per session; this leaves
# headroom for retries while making bulk exfil impractical.
iptables -A OUTPUT -p udp --dport 53 -d 1.1.1.1/32 \
    -m hashlimit --hashlimit-name dns-out \
    --hashlimit-above 60/minute --hashlimit-burst 20 \
    --hashlimit-mode dstip -j DROP
iptables -A OUTPUT -p udp --dport 53 -d 1.0.0.1/32 \
    -m hashlimit --hashlimit-name dns-out2 \
    --hashlimit-above 60/minute --hashlimit-burst 20 \
    --hashlimit-mode dstip -j DROP
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
        # Defense-in-depth: refuse to allowlist a private/link-local
        # address even if DNS returns one. The earlier explicit DROPs
        # would catch this anyway; this makes the misbehavior obvious
        # in logs instead of silently dropping every Anthropic call.
        case "$ip" in
            10.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*|192.168.*|169.254.*|100.6[4-9].*|100.[7-9][0-9].*|100.1[0-1][0-9].*|100.12[0-7].*|127.*)
                echo "[kato-sandbox] ERROR: refusing to allowlist private/loopback IP $ip for $domain — DNS poisoning?" >&2
                exit 1
                ;;
        esac
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

# ----- Self-verify (all checks fail-closed) -----
#
# If api.anthropic.com is unreachable, Claude cannot do its job.
# Continuing to spawn it in that state would just produce confusing
# error spirals (retries hammering a dead endpoint, MCP fallbacks
# activating, etc.) — and is potentially dangerous if the operator
# misreads the failure as "Claude is working but slow." Fail closed.
if ! curl --connect-timeout 5 -sI https://api.anthropic.com/ >/dev/null 2>&1; then
    echo "[kato-sandbox] ERROR: cannot reach api.anthropic.com — refusing to start (firewall and/or upstream broken)" >&2
    exit 1
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
