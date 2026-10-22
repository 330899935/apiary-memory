# Security Policy

## Reporting a vulnerability

Email **security@hello-fengchao.com** with a description and reproduction steps. Please do not
open a public issue for anything that could expose user memory.

We aim to acknowledge within **3 days** and to ship a fix or a mitigation plan within **14 days**
for confirmed issues. This is a one-person project, so those are honest targets rather than an
SLA — if you have not heard back in a week, mail again.

## Threat model

The whole point of this package is to keep memory off other people's servers, so the threat
model is short and deliberately narrow.

**What this package is:** a stateless stdio bridge. It speaks MCP on stdin/stdout and forwards
requests to a Hive instance on `127.0.0.1`. It opens no data files, caches no state, and
duplicates no engine.

**What it can and cannot do:**

| Property | Reality |
| --- | --- |
| Network egress | Only to `HIVE_MCP_URL`, which defaults to `http://127.0.0.1:8768/mcp`. Nothing else is contacted. There is no telemetry, no analytics, no update check. |
| Data at rest | The bridge stores nothing. Memory lives in the Hive app's own store on your disk. |
| Credentials | `HIVE_AGENT_ID` and `HIVE_AGENT_TOKEN` are read from the environment. Do not commit them; do not paste them into an issue. |
| Write authority | An agent can only `propose`. A proposal enters a pending queue and requires human confirmation inside the Hive. No tool in this package can confirm, self-approve, or bypass that queue. |
| Vault isolation | Enforced by the Hive, server-side — not by prompt instructions. Cloud agents (`mcp` / `mobile`) cannot read private vaults; agents absent from the registration table are sealed by default. |

**Out of scope for this repository:** vulnerabilities in the Hive app itself (report them to the
same address, they are still ours), and anything requiring an attacker who already has code
execution on the user's machine — at that point the memory file is readable directly and the
bridge is not the interesting target.

## Dependency surface

`dependencies = []`. The bridge has no third-party runtime dependencies, which is intentional:
every dependency here would be a dependency in the user's trust boundary.
