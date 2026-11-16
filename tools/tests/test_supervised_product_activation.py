"""Exercise signed owner activation through the fixed production dispatch path."""

from __future__ import annotations

import base64
import hashlib
import os
import sqlite3
import subprocess
import tempfile
import unittest
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tools import autonomy_broker_host as host
from tools import autonomy_guardian
from tools import supervised_development_product as product
from tools import supervised_development_runtime as runtime
from tools.autonomy_broker import Denied, Ledger, canonical
from tools.supervised_development_broker import DOMAIN, MANUAL_DOMAIN, DevelopmentAdmissionBroker
from tools.supervised_development_controller import SupervisedDevelopmentController
from tools.supervised_development_pipeline import GovernorTask, SupervisedDevelopmentPipeline

NOW = 1_800_000_000
SOURCE = "a" * 40
OWNER_UID = 1000


class FakeGuardian:
    def __init__(self):
        self.state = {"state": "STOPPED", "trusted": True, "generation": str(uuid.uuid4())}
        self.resumes = []
        self.stops = []

    def status(self):
        return dict(self.state)

    def resume(self, root_uid, actor_uid, ready, started, *, expected_generation):
        self.resumes.append((root_uid, actor_uid, expected_generation))
        if expected_generation != self.state["generation"] or not ready() or not started():
            raise AssertionError("production activation readiness was not satisfied")
        self.state.update(
            state="ACTIVE",
            previous_generation=expected_generation,
            generation=str(uuid.uuid4()),
        )

    def stop(self, actor_uid):
        self.stops.append(actor_uid)
        self.state.update(state="STOPPED", generation=str(uuid.uuid4()))


class ProductionActivationTests(unittest.TestCase):
    @contextmanager
    def fixture(self, *, version=2, approved=True):
        """Keep real signatures and databases; replace only privileged boundaries."""
        with ExitStack() as stack:
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            clock = stack.enter_context(patch("time.time", return_value=NOW))
            controller = SupervisedDevelopmentController(directory / "controller.sqlite3")
            stack.callback(controller.close)
            guardian = FakeGuardian()
            task = GovernorTask(
                objective="Improve a bounded diagnostic.",
                source_sha=SOURCE,
                allowed_paths=("tools/example.py",),
                provider="openai",
                model="gpt-5.3-codex",
                max_calls=1,
                max_cost_usd=1.0,
                deadline_seconds=120,
                owner_id="aaron",
                request_ref=str(uuid.uuid4()),
                candidate_id=81,
                guardian_generation=guardian.state["generation"],
                expires_at=0 if version == 2 else NOW + 60,
                approval_version=version,
            )
            task_id = SupervisedDevelopmentPipeline(controller, None, None).submit(task)
            pending = controller.pending_owner_task()
            self.assertIsNotNone(pending)

            private = ec.generate_private_key(ec.SECP256R1())
            public = private.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            key_id = hashlib.sha256(public).hexdigest()
            keys = {key_id: public}

            def sign(body, domain):
                value = {k: v for k, v in body.items() if k not in {"key_id", "signature"}}
                value["key_id"] = key_id
                value["signature"] = base64.b64encode(
                    private.sign(domain + canonical(value), ec.ECDSA(hashes.SHA256()))
                ).decode()
                return value

            def ledger(name):
                path = directory / name
                path.mkdir(mode=0o700)
                value = Ledger(path / "operations.sqlite3", owner_uid=os.getuid())
                stack.callback(value.close)
                return path / "operations.sqlite3", value

            admission_path, admission = ledger("admission")
            _, audit = ledger("operator")
            broker = DevelopmentAdmissionBroker(
                admission,
                relay_uid=OWNER_UID,
                worker_uid=1001,
                public_keys=keys,
                guardian=guardian.status,
            )
            approval = sign(
                {
                    ("request_id" if k == "request_ref" else k): v
                    for k, v in pending.items()
                    if k != "goal"
                },
                MANUAL_DOMAIN if version == 2 else DOMAIN,
            )
            if approved:
                broker.approve(canonical(approval), OWNER_UID)

            proof = {
                "version": 1,
                "status": "ready",
                "task_id": task_id,
                "request_id": pending["request_ref"],
                "parameters_sha256": pending["parameters_sha256"],
                "source_sha": SOURCE,
                "approval_version": version,
                "expires_at": pending["expires_at"],
            }

            def readonly_ledger(path):
                self.assertEqual(path, admission_path)
                connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                connection.execute("PRAGMA query_only=ON")
                verifier = Ledger.__new__(Ledger)
                verifier.db = connection
                try:
                    verifier.verify_audit()
                except BaseException:
                    connection.close()
                    raise
                return connection

            source = {"sha": SOURCE, "status": ""}

            def source_git(root, *args):
                self.assertEqual(root, host.CANONICAL)
                self.assertIn(args, {("rev-parse", "HEAD"), ("status", "--porcelain")})
                return source["sha" if args == ("rev-parse", "HEAD") else "status"]

            def systemctl(args, **kwargs):
                self.assertIn(
                    args,
                    [
                        ["/usr/bin/systemctl", "is-active", autonomy_guardian.UNIT],
                        ["/usr/bin/systemctl", "is-enabled", autonomy_guardian.UNIT],
                    ],
                )
                self.assertEqual(kwargs["env"], host.SAFE_ENV)
                return subprocess.CompletedProcess(
                    args,
                    3 if args[1] == "is-active" else 1,
                    stdout=b"inactive\n" if args[1] == "is-active" else b"disabled\n",
                )

            def fixed_systemctl(*args):
                self.assertEqual(args, ("is-active", "--quiet", product.CONTROLLER_UNIT))
                return True

            stack.enter_context(patch.object(product, "CONTROLLER_DATABASE", controller.database))
            stack.enter_context(patch.object(product, "ADMISSION_DATABASE", admission_path))
            stack.enter_context(patch.object(product, "readonly_ledger", readonly_ledger))
            stack.enter_context(patch.object(product, "read_readiness", lambda: dict(proof)))
            stack.enter_context(patch.object(runtime, "_trusted_source_git", source_git))
            system_calls = stack.enter_context(patch("subprocess.run", side_effect=systemctl))
            fixed_calls = stack.enter_context(
                patch.object(autonomy_guardian, "fixed_systemctl", side_effect=fixed_systemctl)
            )
            operator = product.production_operator(audit, keys, guardian)

            def activation(**changes):
                body = {
                    "version": 1,
                    "action": "activate_development",
                    "request_id": str(uuid.uuid4()),
                    "task_id": task_id,
                    "approval_id": pending["request_ref"],
                    "source_sha": SOURCE,
                    "parameters_sha256": pending["parameters_sha256"],
                    "guardian_generation": pending["guardian_generation"],
                    "expires_at": int(clock.return_value) + 30,
                    **changes,
                }
                return sign(body, product.DOMAINS["activate_development"])

            yield SimpleNamespace(
                operator=operator,
                controller=controller,
                guardian=guardian,
                approval=approval,
                pending=pending,
                activation=activation,
                task_id=task_id,
                clock=clock,
                source=source,
                system_calls=system_calls,
                fixed_calls=fixed_calls,
            )

    def assert_stopped(self, fixture):
        self.assertEqual(fixture.guardian.state["state"], "STOPPED")
        self.assertEqual(fixture.guardian.resumes, [])
        self.assertEqual(fixture.guardian.stops, [])
        fixture.system_calls.assert_not_called()
        fixture.fixed_calls.assert_not_called()

    def test_manual_approval_after_days_accepts_fresh_separate_activation_once(self):
        with self.fixture() as fixture:
            fixture.clock.return_value += 30 * 86400
            body = fixture.activation()
            self.assertEqual(fixture.pending["expires_at"], 0)
            self.assertNotEqual(body["request_id"], fixture.approval["request_id"])
            result = fixture.operator.dispatch(body, OWNER_UID)
            self.assertEqual(
                result,
                {"status": "active", "task_id": fixture.task_id, "request_id": body["request_id"]},
            )
            self.assertEqual(
                fixture.guardian.resumes, [(0, OWNER_UID, fixture.pending["guardian_generation"])]
            )
            self.assertEqual(fixture.guardian.state["state"], "ACTIVE")
            self.assertEqual(fixture.system_calls.call_count, 2)
            self.assertEqual(fixture.fixed_calls.call_count, 2)
            self.assertEqual(fixture.guardian.stops, [])
            self.assertEqual(fixture.controller.snapshot(fixture.task_id)["provider_calls"], 0)

            # A receipt replay cannot resume a new STOPPED cycle, even after expiry.
            fixture.clock.return_value += 301
            fixture.guardian.state.update(state="STOPPED", generation=str(uuid.uuid4()))
            self.assertEqual(fixture.operator.dispatch(body, OWNER_UID), result)
            self.assertEqual(len(fixture.guardian.resumes), 1)
            self.assertEqual(fixture.guardian.state["state"], "STOPPED")
            self.assertEqual(fixture.system_calls.call_count, 2)
            altered = fixture.activation(
                request_id=body["request_id"], approval_id=str(uuid.uuid4())
            )
            with self.assertRaisesRegex(Denied, "request_identity_conflict"):
                fixture.operator.dispatch(altered, OWNER_UID)
            self.assertEqual(len(fixture.guardian.resumes), 1)

    def test_legacy_activation_is_capped_by_the_signed_approval_expiry(self):
        with self.fixture(version=1) as fixture:
            body = fixture.activation(expires_at=NOW + 61)
            with self.assertRaisesRegex(Denied, "development_activation_changed"):
                fixture.operator.dispatch(body, OWNER_UID)
            self.assert_stopped(fixture)
            with self.assertRaisesRegex(Denied, "reconciliation_required"):
                fixture.operator.dispatch(body, OWNER_UID)
            self.assert_stopped(fixture)

        with self.fixture(version=1) as fixture:
            accepted = fixture.operator.dispatch(fixture.activation(expires_at=NOW + 60), OWNER_UID)
            self.assertEqual(accepted["status"], "active")
            self.assertEqual(len(fixture.guardian.resumes), 1)

    def test_manual_approval_still_requires_fresh_activation(self):
        with self.fixture() as fixture:
            for expiry in (0, NOW - 1, NOW, NOW + 301):
                with self.subTest(expiry=expiry):
                    with self.assertRaisesRegex(Denied, "development_product_expired"):
                        fixture.operator.dispatch(fixture.activation(expires_at=expiry), OWNER_UID)
                    self.assert_stopped(fixture)

    def test_missing_signed_owner_approval_is_denied(self):
        with self.fixture(approved=False) as fixture:
            with self.assertRaisesRegex(Denied, "development_owner_approval_required"):
                fixture.operator.dispatch(fixture.activation(), OWNER_UID)
            self.assert_stopped(fixture)

    def test_approval_signature_cannot_authorize_the_separate_activation(self):
        with self.fixture() as fixture:
            body = fixture.activation()
            body["signature"] = fixture.approval["signature"]
            with self.assertRaisesRegex(Denied, "operator_signature"):
                fixture.operator.dispatch(body, OWNER_UID)
            self.assert_stopped(fixture)

    def test_activation_cannot_cross_guardian_generations(self):
        with self.fixture() as fixture:
            body = fixture.activation()
            fixture.guardian.state["generation"] = str(uuid.uuid4())
            with self.assertRaisesRegex(Denied, "development_product_stale_generation"):
                fixture.operator.dispatch(body, OWNER_UID)
            self.assert_stopped(fixture)

    def test_valid_signature_cannot_change_the_approved_request_binding(self):
        with self.fixture() as fixture:
            for field, value in (
                ("approval_id", str(uuid.uuid4())),
                ("parameters_sha256", "b" * 64),
                ("source_sha", "b" * 40),
            ):
                with self.subTest(field=field):
                    with self.assertRaisesRegex(Denied, "development_activation_changed"):
                        fixture.operator.dispatch(fixture.activation(**{field: value}), OWNER_UID)
                    self.assert_stopped(fixture)

    def test_source_drift_blocks_a_valid_manual_activation(self):
        for changed in ({"sha": "b" * 40}, {"status": " M tools/example.py\n"}):
            with self.subTest(changed=changed), self.fixture() as fixture:
                fixture.source.update(changed)
                with self.assertRaisesRegex(Denied, "development_source_drift"):
                    fixture.operator.dispatch(fixture.activation(), OWNER_UID)
                self.assert_stopped(fixture)


if __name__ == "__main__":
    unittest.main()
