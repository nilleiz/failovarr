# Deployment Modes

## Cold Standby

Only one container is active on the production network. Both stopped/started
containers may use the same static client/macvlan IP only after the old
container is confirmed stopped. Configure Shared Storage and keep Main's and
Follower's persistent node-local state separate.

Before stopping the authoritative node, use **Save and export now**. A direct
`docker stop` does not guarantee that Dispatcharr runs a plugin stop hook, so
it cannot guarantee a final bundle. Start the standby node, let its enabled
service import the latest verified bundle once, then validate client access.

## Online with plugin-managed VIP

Both nodes need different management IPs. The plugin manages only a secondary
client IPv4 address inside the container network namespace; it does not change
Docker, host networking, DNS, Cloudflare or a router.

This Linux-only mode requires `NET_ADMIN` and `NET_RAW`. The plugin rejects an
already-owned VIP and refuses to remove the primary management address. Do not
automate a takeover without reliable fencing or a third witness.

## Online with external HA reverse proxy

The user provides the highly available proxy and routes client traffic only to
nodes where `GET http://<management-ip>:9192/v1/readiness` returns HTTP 200.
The proxy itself remains a required HA component. The plugin does not manage
its configuration.

## Network rules

- Online nodes always use distinct management IPs.
- Permit port 9192 only on the management network and only to required peers
  and operators.
- Direct Pull authenticates requests with short-lived HMAC signatures but does
  not encrypt traffic. Use TLS, WireGuard, a VLAN and firewall policy as needed.
- Cloudflare Tunnel and VPN should target the stable VIP or HA frontend, not
  two independent writers.
