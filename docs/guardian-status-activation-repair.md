# Guardian status socket-activation repair

Base: `0b906174551ebc0d8f9ffbf4a6005a95abc29948` (merged PR #110).
Worktree: `/home/aaron/jarvis-guardian-status-activation-fix-worktree`.
This is a source repair for review, **not** authorization to install or restart
the protected status service. Guardian/broker code, ownership, policy, manifests,
hash validation, signed controls, Developer client and Android are unchanged.

## Reproduced failure, not an inferred socket mismatch

The installed status module SHA256 matches the merged source:
`2ce4d21db93cf38b362be9e8845d4072f9d468e10dc6e7209d54388c659294bc`.
Read-only systemd inspection showed service `exit-code / ExecMainStatus=1`, socket
`service-start-limit-hit`, correct association and no drop-ins. Journal records
showed repeated short-lived processes followed by the socket start-limit failure.
No reset-failed, restart or activation request was made to those installed units.

A bounded disposable container ran with EUID 0, empty capability set,
no-new-privileges, read-only filesystem, no network and host PID visibility solely
to inspect the two namespace metadata paths. No production files were writable.
The unchanged main returned 1. The precise failing expression was:

```
os.stat("/proc/1/ns/user") -> PermissionError, errno 13
```

The service's empty capability set does not confer ptrace permission to dereference
PID1's nsfs link. The failure happens **before** activation environment, inherited
FD, listener-path and Guardian-loader checks. Thus correct LISTEN_PID/FDS and
socket association cannot rescue the old code. The failed socket's existing
RemoveOnStop behavior explains the absent pathname and client UNKNOWN; it is not
the cause of the initial process exit.

The new deterministic regression failed on the unchanged implementation:
the trusted loader was never reached after PID1 metadata access was denied.

## Minimal correction and security review

Do not add CAP_SYS_PTRACE or relax sandboxing. Validate the kernel's own-process
UID/GID maps against the full `0 0 4294967295` identity mapping. Missing, unreadable,
partial, malformed or remapped maps fail closed. However, **mapping equality alone
is not proof of namespace identity**: a privileged parent can create an identity-
mapped child namespace. The initial mapping-only proposal was therefore augmented
before delivery, not shipped as sufficient authentication.

The system manager supplies one additional read-only descriptor:

```
OpenFile=/proc/self/ns/user:guardian-host-userns:read-only
```

Systemd opens it on the manager side, before the service loses privileges. The
service requires NS_GET_NSTYPE to identify a real user namespace, and compares both
device and inode with its own namespace. A privileged child with identical maps
still has a distinct namespace and is rejected. Errors (including inaccessible
ancestor namespace descriptors) are denied. The anchor is closed after validation;
it is not a new authority to enter namespaces. Capabilities remain empty and
RestrictNamespaces remains enabled. No CAP_SYS_ADMIN or CAP_SYS_PTRACE is granted.

Activation now requires exact LISTEN_PID, LISTEN_FDS=2 and exactly the two unique
names `guardian-status` and `guardian-host-userns`. The socket unit names its FD
explicitly. Descriptors start at 3; names select the socket and anchor without
assuming their relative order. The socket must still be AF_UNIX/SOCK_STREAM,
listening, and bound to the fixed status pathname. Old/incomplete units fail
closed rather than silently accepting a missing namespace anchor.

This requires systemd >=253 (OpenFile); the tested host is systemd 259. No shell
wrapper, namespace bypass, new policy, credential, writable exception, command
dispatcher or unsigned STOP/RESUME path is introduced. Existing client PID1 peer
authentication, freshness challenge, bounds and UNKNOWN fallback are unchanged.

The trusted system manager remains the authority for the anchor and socket. This
is not protection against a hostile host root/system manager intentionally replacing
the unit or forging its descriptor contract. It does reject an accidentally or
deliberately remapped service namespace, including equal-map/different-inode cases.

## Regression and diagnostic layers

1. Root-free tests reproduce errno13, preserve exact activation admission checks,
   reject wrong FD names/counts/PID, invalid descriptor/type/path/listen state,
   non-namespace and non-user-namespace anchors, changed inode/device, full-map
   child-namespace mismatch and malformed identity maps. Anchors are closed.
2. Real `systemd-socket-activate` supplies socket FD3 and LISTEN_PID/FDS/FDNAMES.
   Since that utility accepts only sockets, its fixture adds a real namespace FD
   to model OpenFile delivery. Only EUID and the Guardian fixture/path are replaced
   in this unprivileged test; namespace and listener validation are real. A valid
   request succeeds; an unsigned resume frame is rejected without killing the listener.
3. A uniquely named, disposable **user-systemd** socket/service exercises actual
   OpenFile delivery and named descriptor activation without an installed unit.
   The root identity/controller fixture is substituted, not production state.
   A real restricted user namespace is also tested and denied. The service has
   a 15-second runtime bound, one-start limit, and exact-unit cleanup in finally.
   This test skips explicitly on CI hosts without a user systemd manager.
4. A capability-free EUID0 diagnostic with host PID visibility reproduces errno13
   while the candidate's real namespace-anchor validation succeeds. This does not
   install or execute the new privileged service on the host.
5. Existing socket/client tamper, malformed credentials, missing endpoint, stale
   response/generation, bounded framing and signed-control regression tests remain
   part of the focused/full suites. Restricted-client success uses existing peer
   credential contract fixtures; live root-unit/sandbox end-to-end success is **not
   claimed** before the separately authorized installation/restart.

No privileged identity-mapped child was created on this host: rejection of that
case is covered by explicit full-map plus namespace identity mismatch tests. The
actual unprivileged remapped-namespace denial and namespace descriptor behavior
were executed. No test replaces historical production call records or credentials.

## Separately authorized installation and rollback plan — NOT executed

The module and **both** status unit files form one upgrade contract. A future
operator-approved update must verify canonical candidate hashes, current installed
hashes, root ownership and Guardian STOPPED/trusted first. Preserve a private copy
of precisely those three existing payloads plus unit enablement/state. Refuse
unknown destination contents or missing systemd OpenFile support.

After authorization, stop only `jarvis-autonomy-status.socket` and
`jarvis-autonomy-status.service`, replace those reviewed payloads together using
root-owned files/modes, verify unit definitions, reload the system manager, clear
only these two units' recorded failures, and start only the status socket. No
Guardian/broker restart, manifest rewrite or gateway deployment is needed for this
status-reader repair. A query must activate it successfully before any dashboard
claim. Compare host and restricted-client state/trust/generation, then exercise
malformed/read-only requests. Guardian must remain STOPPED and generation unchanged.

If validation fails, stop only these status units and restore the captured three
payloads together, followed by a manager reload. Because the previous reader has
this known startup defect, leave its socket inactive rather than retrying it into
a start-limit loop. UNKNOWN is the correct degraded client state. Preserve signed
operator controls and the independent Guardian CLI fallback. Do not restore any
Guardian state, broker ledger, user data or credential from a stale backup.

## Results and boundaries

Focused tests: **119 passed** locally, including the real user-manager activation
and restricted-namespace denial. Local interpreter: Python 3.14.4; Python3.12
requires separate protected CI evidence. Full Python regressions: **2,447 passed,
3 skipped, 143 subtests passed**, in 48.86 seconds. The three existing privileged
deployment/rollback gates were not enabled. Ruff/format (276 files), mypy (121
active-source files plus the status module), compileall, Bandit, dependency audit,
product baseline, systemd verification, pinned actionlint and pinned current-tree
Gitleaks passed. Gitleaks found zero leaks. Systemd warnings concern only unrelated
host XFS units' obsolete CPUAccounting option. Exact-head CI and separate PR CodeQL
results are recorded in the PR evidence after completion; they are not inferred
from local Python3.14 tests. Existing canonical Core security alerts are unchanged.

Evidence directory: `/tmp/jarvis-status-startup-evidence.6IKPjd/`.
Guardian remains STOPPED/trusted=true, generation
`0c8b01fb-1347-4d2b-83b7-9a9c1e80ef89`. Production Developer PID remains 1906122.
The existing failed status units remain unchanged and failed. No merge, root
installation, production restart/deployment, enrollment, APK/OTA change, signing,
Guardian/broker mutation, autonomous execution or Phase2 activation was performed.
Phase1 remains uncertified pending actual installed-service and physical acceptance.
