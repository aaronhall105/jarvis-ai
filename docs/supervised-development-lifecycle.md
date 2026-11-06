# Supervised development lifecycle contract

This controller is dormant lifecycle/state orchestration. It does not call a
provider, create a worktree, apply edits, run tests, write Git history, create a
PR, merge, deploy, or control Guardian.

The existing certified Phase 2 harness remains responsible for provider budget,
credential separation, deadlines, structured JSON validation, source checksums,
allow-list/path/symlink checks, atomic edit application, tests, static checks,
cleanup and generated diff evidence. A future trusted adapter must verify those
results before submitting evidence here.

The controller currently records only an approval request. It has no execution
admission authority: `start()` always fails closed with
`trusted_execution_admission_unavailable`. No caller-supplied callback, database
label or test fixture can override this. A future trusted Governor-to-worker
adapter must authenticate the owner through the protected broker/operator
authority, bind the authorization to the task and immutable parameter hash, and
perform admission outside this dormant controller. Review and evidence are also
recorded as pending data; no review field grants execution authority.

`REVIEW_REQUIRED` is a human handoff only. It grants no commit, push, PR, merge,
deployment, release, or Guardian authority. Governor-to-worker execution,
independent-review execution, and GitHub integration are not connected yet.
