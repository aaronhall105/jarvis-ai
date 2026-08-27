import unittest
import uuid
from datetime import datetime, timezone

from app.service_connectors import (
    CapabilityAccess,
    AvailabilityWindow,
    BrowserActionResult,
    BrowserElement,
    BrowserPageState,
    CalendarEvent,
    CalendarWriteResult,
    CapabilityMetadata,
    ConnectorUnavailableError,
    Contact,
    ContactResolution,
    EmailAddress,
    EmailDraft,
    EmailMessage,
    EmailSendReceipt,
    EmailThread,
    ExecutionError,
    ExternalActionReceipt,
    ProviderStatus,
    ReceiptStatus,
    SocialDraft,
    SocialPublishReceipt,
    UNAVAILABLE_CONNECTOR_CATALOG,
    UnavailableConnector,
)


def receipt(capability, status, *, verified, reference=None):
    now = datetime.now(timezone.utc).isoformat()
    return ExternalActionReceipt(
        action_id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        conversation_id="conversation-1",
        capability_id=capability,
        provider_id="fixture",
        target="fixture-target",
        requested_operation=capability.rsplit(".", 1)[-1],
        status=status,
        request_digest="fixture-request-digest",
        idempotency_digest=str(uuid.uuid4()),
        provider_reference=reference,
        result={},
        verification={"verified": verified},
        error=None if status is not ReceiptStatus.FAILED else "fixture failure",
        started_at=now,
        completed_at=now,
    )


class FixtureBrowser:
    """Deterministic semantic browser used only by these tests."""

    def __init__(self):
        self.history = []
        self.fail_click = False
        self.state = BrowserPageState("about:blank", "Blank")

    @property
    def capabilities(self):
        capabilities = tuple(
            CapabilityMetadata(
                name,
                "fixture-browser",
                name,
                access=CapabilityAccess.WRITE,
            )
            for name in (
                "browser.open",
                "browser.click",
                "browser.type",
                "browser.select",
                "browser.scroll",
                "browser.wait",
                "browser.back",
                "browser.upload",
                "browser.download",
            )
        ) + tuple(
            CapabilityMetadata(
                name,
                "fixture-browser",
                name,
                access=CapabilityAccess.READ,
            )
            for name in ("browser.inspect", "browser.extract", "browser.current_state")
        )
        return capabilities

    async def status(self):
        return ProviderStatus(
            "fixture-browser",
            "Fixture Browser",
            True,
            True,
            True,
            potential_capabilities=tuple(item.capability_id for item in self.capabilities),
            executable_capabilities=tuple(item.capability_id for item in self.capabilities),
        )

    async def open(self, url):
        self.history.append(self.state)
        if url.endswith("/private"):
            self.state = BrowserPageState(
                "https://fixture.test/login",
                "Sign in",
                auth_required=True,
                redirect_chain=(url, "https://fixture.test/login"),
            )
        else:
            self.state = BrowserPageState(
                url,
                "Fixture page",
                elements=(BrowserElement("continue", "button", "Continue"),),
                text="Ready",
            )
        return BrowserActionResult(True, "open", self.state, self.state.url != "about:blank")

    async def inspect(self):
        return self.state

    async def click(self, element_id):
        element = next(
            (item for item in self.state.elements if item.element_id == element_id), None
        )
        if element is None:
            return BrowserActionResult(
                False,
                "click",
                self.state,
                False,
                error=ExecutionError("missing_element", "Semantic element is no longer present"),
            )
        if self.fail_click:
            return BrowserActionResult(
                False,
                "click",
                self.state,
                False,
                error=ExecutionError("provider_failure", "Click was rejected"),
            )
        self.history.append(self.state)
        self.state = BrowserPageState(
            "https://fixture.test/complete", "Complete", text="Action completed"
        )
        return BrowserActionResult(True, "click", self.state, state_verified=True)

    async def type(self, element_id, text):
        del element_id, text
        return BrowserActionResult(True, "type", self.state, True)

    async def select(self, element_id, value):
        del element_id, value
        return BrowserActionResult(True, "select", self.state, True)

    async def scroll(self, direction, amount=None):
        del direction, amount
        return BrowserActionResult(True, "scroll", self.state, True)

    async def wait(self, condition, timeout_seconds):
        del condition, timeout_seconds
        return BrowserActionResult(True, "wait", self.state, True)

    async def back(self):
        if self.history:
            self.state = self.history.pop()
        return BrowserActionResult(True, "back", self.state, True)

    async def upload(self, element_id, file_reference):
        del element_id, file_reference
        return BrowserActionResult(True, "upload", self.state, True)

    async def download(self, element_id):
        del element_id
        return BrowserActionResult(
            True, "download", self.state, True, download_reference="fixture-download"
        )

    async def extract(self, selector=None):
        return {"selector": selector, "text": self.state.text, "url": self.state.url}

    async def current_state(self):
        return self.state


class FixtureEmail:
    def __init__(self):
        address = EmailAddress("sender@example.test", "Sender")
        message = EmailMessage(
            "message-1",
            "thread-1",
            address,
            (EmailAddress("user@example.test", "User"),),
            "Dinner",
            "Are you free Friday?",
            "2026-08-26T10:00:00+00:00",
            unread=True,
            important=True,
        )
        self.threads = {"thread-1": EmailThread("thread-1", (message,))}
        self.drafts = {}
        self.sent = []
        self.fail_send = False

    async def search(self, query, *, unread=None, important=None):
        matches = [
            thread
            for thread in self.threads.values()
            if query.casefold() in thread.messages[0].subject.casefold()
        ]
        if unread is True:
            matches = [thread for thread in matches if thread.messages[-1].unread]
        if important is True:
            matches = [thread for thread in matches if thread.messages[-1].important]
        return matches

    async def read_thread(self, thread_id):
        return self.threads[thread_id]

    async def create_draft(self, recipients, subject, body_text, *, thread_id=None):
        draft = EmailDraft("draft-1", tuple(recipients), subject, body_text, thread_id)
        self.drafts[draft.draft_id] = draft
        return draft

    async def send_draft(self, draft_id, *, conversation_id, approval_reference):
        del conversation_id, approval_reference
        if self.fail_send:
            action = receipt("email.send", ReceiptStatus.FAILED, verified=False)
            return EmailSendReceipt("fixture", None, None, False, False, "failed", action)
        draft = self.drafts[draft_id]
        self.sent.append(draft)
        action = receipt(
            "email.send",
            ReceiptStatus.ACCEPTED_UNVERIFIED,
            verified=False,
            reference="provider-message-2",
        )
        return EmailSendReceipt(
            "fixture",
            "provider-message-2",
            draft.thread_id,
            accepted=True,
            delivery_confirmed=False,
            provider_status="accepted",
            action_receipt=action,
        )


class FixtureCalendar:
    def __init__(self):
        self.events = [
            CalendarEvent(
                "existing",
                "primary",
                "Existing",
                "2026-08-28T18:00:00+00:00",
                "2026-08-28T19:00:00+00:00",
                "UTC",
            )
        ]
        self.fail_create = False

    async def timezone(self):
        return "UTC"

    async def list_events(self, starts_at, ends_at):
        return [
            event
            for event in self.events
            if event.starts_at < ends_at and event.ends_at > starts_at
        ]

    async def availability(self, starts_at, ends_at):
        conflicts = await self.list_events(starts_at, ends_at)
        return AvailabilityWindow(
            starts_at,
            ends_at,
            not conflicts,
            tuple(event.event_id for event in conflicts),
        )

    async def create_event(self, event, *, conversation_id, approval_reference=None):
        del conversation_id, approval_reference
        if self.fail_create:
            return CalendarWriteResult(
                None, None, error=ExecutionError("provider_failure", "Create failed")
            )
        availability = await self.availability(event.starts_at, event.ends_at)
        if not availability.available:
            return CalendarWriteResult(None, None, conflict=availability)
        self.events.append(event)
        return CalendarWriteResult(
            event,
            receipt(
                "calendar.create",
                ReceiptStatus.VERIFIED,
                verified=True,
                reference=event.event_id,
            ),
        )


class FixtureContacts:
    def __init__(self):
        self.contacts = (
            Contact("dave-1", "Dave Smith", ("dave.smith@example.test",)),
            Contact("dave-2", "Dave Jones", ("dave.jones@example.test",)),
            Contact("amber", "Amber", ("amber@example.test",)),
        )

    async def search(self, query):
        folded = query.casefold().strip()
        exact_matches = tuple(
            contact for contact in self.contacts if contact.display_name.casefold() == folded
        )
        matches = exact_matches or tuple(
            contact for contact in self.contacts if folded in contact.display_name.casefold()
        )
        exact = len(exact_matches) == 1
        return ContactResolution(query, matches, exact=exact, ambiguous=len(matches) > 1)


class FixtureSocial:
    def __init__(self):
        self.drafts = {}
        self.published = []
        self.fail_publish = False

    async def create_draft(self, text, media_references=()):
        draft = SocialDraft("social-draft-1", text, tuple(media_references))
        self.drafts[draft.draft_id] = draft
        return draft

    async def publish(self, draft_id, *, conversation_id, approval_reference):
        del conversation_id, approval_reference
        if self.fail_publish:
            action = receipt("social.publish", ReceiptStatus.FAILED, verified=False)
            return SocialPublishReceipt(None, False, "failed", action)
        self.published.append(self.drafts[draft_id])
        action = receipt(
            "social.publish", ReceiptStatus.VERIFIED, verified=True, reference="post-1"
        )
        return SocialPublishReceipt("post-1", True, "published", action)


class UnavailableConnectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_covers_external_services_without_exposing_capabilities(self):
        expected = {
            "browser",
            "gmail",
            "calendar",
            "contacts",
            "communication",
            "instagram",
            "facebook",
            "tiktok",
            "x_social",
            "travel",
            "shopping",
            "dating",
        }
        self.assertEqual(expected, set(UNAVAILABLE_CONNECTOR_CATALOG))
        for service in expected:
            connector = UnavailableConnector.for_service(service)
            status = await connector.status()
            self.assertFalse(status.configured)
            self.assertFalse(status.authenticated)
            self.assertFalse(status.healthy)
            self.assertFalse(status.available)
            self.assertEqual((), status.executable_capabilities)
            self.assertTrue(status.potential_capabilities)
            self.assertTrue(connector.setup.capabilities_after_setup)
            self.assertFalse(hasattr(connector, "capabilities"))

    async def test_browser_setup_lists_every_semantic_primitive_as_potential_only(self):
        connector = UnavailableConnector.for_service("browser")
        potential = {item.capability_id for item in connector.setup.capabilities_after_setup}
        self.assertEqual(
            {
                "browser.open",
                "browser.inspect",
                "browser.click",
                "browser.type",
                "browser.select",
                "browser.scroll",
                "browser.wait",
                "browser.back",
                "browser.upload",
                "browser.download",
                "browser.extract",
                "browser.current_state",
            },
            potential,
        )
        with self.assertRaises(ConnectorUnavailableError):
            await connector.execute("browser.open", {"url": "https://example.test"})

    async def test_redacted_status_contains_requirements_not_secret_values(self):
        connector = UnavailableConnector.for_service("gmail")
        status = (await connector.status()).as_dict()
        rendered = repr({"provider": status, "setup": connector.setup.to_redacted_dict()})
        self.assertIn("oauth_required", rendered)
        self.assertNotIn("access_token", rendered.casefold())
        self.assertNotIn("refresh_token", rendered.casefold())
        potential = {item.capability_id for item in connector.setup.capabilities_after_setup}
        self.assertTrue(
            {
                "gmail.search",
                "gmail.read",
                "gmail.thread",
                "gmail.draft",
                "gmail.reply",
                "gmail.send",
            }.issubset(potential)
        )

    async def test_social_setup_capabilities_are_provider_specific_and_noncolliding(self):
        social_ids = []
        for provider in ("instagram", "facebook", "tiktok", "x_social"):
            connector = UnavailableConnector.for_service(provider)
            potential = {item.capability_id for item in connector.setup.capabilities_after_setup}
            self.assertTrue(all(item.startswith(f"{provider}.") for item in potential))
            draft = next(
                item
                for item in connector.setup.capabilities_after_setup
                if item.capability_id == f"{provider}.draft"
            )
            publish = next(
                item
                for item in connector.setup.capabilities_after_setup
                if item.capability_id == f"{provider}.publish"
            )
            self.assertFalse(draft.requires_confirmation)
            self.assertTrue(publish.requires_confirmation)
            social_ids.extend(potential)
        self.assertEqual(len(social_ids), len(set(social_ids)))

    async def test_calendar_setup_includes_timezone_discovery(self):
        connector = UnavailableConnector.for_service("calendar")
        potential = {item.capability_id for item in connector.setup.capabilities_after_setup}
        self.assertIn("calendar.timezone", potential)


class BrowserContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_navigation_inspection_action_and_state_verification(self):
        browser = FixtureBrowser()
        opened = await browser.open("https://fixture.test/start")
        self.assertTrue(opened.state_verified)
        self.assertEqual("Ready", (await browser.inspect()).text)
        clicked = await browser.click("continue")
        self.assertTrue(clicked.success)
        self.assertTrue(clicked.state_verified)
        self.assertEqual("https://fixture.test/complete", (await browser.current_state()).url)

    async def test_missing_element_and_provider_failure_never_report_completion(self):
        browser = FixtureBrowser()
        await browser.open("https://fixture.test/start")
        missing = await browser.click("missing")
        self.assertFalse(missing.success)
        self.assertFalse(missing.state_verified)
        self.assertEqual("missing_element", missing.error.code)

        browser.fail_click = True
        failed = await browser.click("continue")
        self.assertFalse(failed.success)
        self.assertFalse(failed.state_verified)
        self.assertEqual("provider_failure", failed.error.code)

    async def test_redirect_to_auth_is_explicit_and_not_bypassed(self):
        browser = FixtureBrowser()
        result = await browser.open("https://fixture.test/private")
        self.assertTrue(result.state.auth_required)
        self.assertEqual("https://fixture.test/login", result.state.url)
        self.assertEqual(2, len(result.state.redirect_chain))


class EmailContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_read_draft_and_send_receipt_have_distinct_states(self):
        email = FixtureEmail()
        found = await email.search("Dinner", unread=True, important=True)
        self.assertEqual("thread-1", found[0].thread_id)
        self.assertEqual(
            "Are you free Friday?", (await email.read_thread("thread-1")).messages[0].body_text
        )

        draft = await email.create_draft(
            [EmailAddress("sender@example.test")],
            "Re: Dinner",
            "Yes",
            thread_id="thread-1",
        )
        self.assertEqual([], email.sent)
        self.assertEqual("draft-1", draft.draft_id)

        sent = await email.send_draft(
            draft.draft_id, conversation_id="conversation-1", approval_reference="approval-1"
        )
        self.assertTrue(sent.accepted)
        self.assertFalse(sent.delivery_confirmed)
        self.assertEqual("provider-message-2", sent.message_id)
        self.assertEqual(ReceiptStatus.ACCEPTED_UNVERIFIED, sent.action_receipt.status)
        self.assertFalse(sent.action_receipt.verification["verified"])
        self.assertEqual(1, len(email.sent))

    async def test_failed_send_has_no_message_id_and_no_success_claim(self):
        email = FixtureEmail()
        draft = await email.create_draft([EmailAddress("sender@example.test")], "Subject", "Body")
        email.fail_send = True
        result = await email.send_draft(
            draft.draft_id, conversation_id="conversation-1", approval_reference="approval-1"
        )
        self.assertFalse(result.accepted)
        self.assertFalse(result.delivery_confirmed)
        self.assertIsNone(result.message_id)
        self.assertEqual(ReceiptStatus.FAILED, result.action_receipt.status)
        self.assertFalse(result.action_receipt.verification["verified"])


class CalendarAndContactsContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_calendar_lists_checks_availability_creates_and_detects_conflict(self):
        calendar = FixtureCalendar()
        self.assertEqual("UTC", await calendar.timezone())
        events = await calendar.list_events(
            "2026-08-28T17:00:00+00:00", "2026-08-28T20:00:00+00:00"
        )
        self.assertEqual(["existing"], [event.event_id for event in events])
        conflict = await calendar.availability(
            "2026-08-28T18:30:00+00:00", "2026-08-28T19:30:00+00:00"
        )
        self.assertFalse(conflict.available)
        self.assertEqual(("existing",), conflict.conflicting_event_ids)

        conflicting_event = CalendarEvent(
            "new-conflict",
            "primary",
            "Dinner",
            "2026-08-28T18:30:00+00:00",
            "2026-08-28T19:30:00+00:00",
            "UTC",
        )
        rejected = await calendar.create_event(conflicting_event, conversation_id="conversation-1")
        self.assertIsNone(rejected.receipt)
        self.assertIsNotNone(rejected.conflict)

        free_event = CalendarEvent(
            "new-event",
            "primary",
            "Dinner",
            "2026-08-28T20:00:00+00:00",
            "2026-08-28T21:00:00+00:00",
            "UTC",
        )
        created = await calendar.create_event(free_event, conversation_id="conversation-1")
        self.assertEqual("new-event", created.event.event_id)
        self.assertEqual(ReceiptStatus.VERIFIED, created.receipt.status)
        self.assertTrue(created.receipt.verification["verified"])

    async def test_calendar_provider_failure_has_no_event_or_receipt(self):
        calendar = FixtureCalendar()
        calendar.fail_create = True
        event = CalendarEvent(
            "failed",
            "primary",
            "Dinner",
            "2026-08-29T20:00:00+00:00",
            "2026-08-29T21:00:00+00:00",
            "UTC",
        )
        result = await calendar.create_event(event, conversation_id="conversation-1")
        self.assertIsNone(result.event)
        self.assertIsNone(result.receipt)
        self.assertEqual("provider_failure", result.error.code)

    async def test_contacts_exact_ambiguous_and_missing_never_invent_addresses(self):
        contacts = FixtureContacts()
        exact = await contacts.search("Amber")
        self.assertEqual("amber@example.test", exact.resolved.email_addresses[0])

        ambiguous = await contacts.search("Dave")
        self.assertTrue(ambiguous.ambiguous)
        self.assertIsNone(ambiguous.resolved)
        self.assertEqual(2, len(ambiguous.matches))

        missing = await contacts.search("Nonexistent")
        self.assertEqual((), missing.matches)
        self.assertIsNone(missing.resolved)


class SocialContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_draft_does_not_imply_publication_and_publish_has_receipt(self):
        social = FixtureSocial()
        draft = await social.create_draft("Caption")
        self.assertEqual([], social.published)
        self.assertEqual("social-draft-1", draft.draft_id)

        published = await social.publish(
            draft.draft_id,
            conversation_id="conversation-1",
            approval_reference="approval-1",
        )
        self.assertTrue(published.published)
        self.assertEqual("post-1", published.post_id)
        self.assertEqual(ReceiptStatus.VERIFIED, published.action_receipt.status)
        self.assertTrue(published.action_receipt.verification["verified"])
        self.assertEqual(1, len(social.published))

    async def test_failed_publish_never_reports_posted(self):
        social = FixtureSocial()
        draft = await social.create_draft("Caption")
        social.fail_publish = True
        failed = await social.publish(
            draft.draft_id,
            conversation_id="conversation-1",
            approval_reference="approval-1",
        )
        self.assertFalse(failed.published)
        self.assertIsNone(failed.post_id)
        self.assertFalse(failed.action_receipt.verification["verified"])
        self.assertEqual(ReceiptStatus.FAILED, failed.action_receipt.status)
        self.assertEqual([], social.published)

    async def test_unconfigured_social_connector_cannot_post(self):
        connector = UnavailableConnector.for_service("instagram")
        with self.assertRaises(ConnectorUnavailableError):
            await connector.execute("instagram.publish", {"draft_id": "draft-1"})


if __name__ == "__main__":
    unittest.main()
