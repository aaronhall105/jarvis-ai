# Whole-Home Intelligence

Jarvis interprets whole-home questions with model-produced semantics and then
grounds every result deterministically against Home Assistant's area, device,
entity and current-state data.

## Query contract

`HomeQueryPlan` represents the operation, scope, category, predicate and
aggregation. Supported scopes are the whole home, one exact configured area,
an internally grounded explicit entity set, and a durable referenced result
set. The model can select semantic scope and filters, but it cannot provide the
entity identities for a referenced set. Core retrieves those identities from
the principal- and conversation-scoped `WorkingContext` and revalidates them
against fresh Home Assistant state.

`GroundedEntitySet` preserves verified entity, device and area identities,
friendly names, current state, availability, supported features and observation
time. A set may contain zero, one or many members; multi-member queries are not
collapsed to a best textual match. `off`, `unknown` and Home Assistant's
explicit `unavailable` state remain distinct.

`HomeSnapshot` is the reusable read-only whole-home view. It derives only
categories backed by current registry/state evidence, including presence,
lights, switches, active media/appliances, cameras, battery health, climate,
locks and security entities. It does not grant write authority and alpha35 does
not use it for unsolicited notifications.

## References and actions

Query results use the existing generic context-projection contract. Their
ordered exact entity set therefore survives a Core restart with the existing
dialogue store and cannot cross a principal or conversation boundary. Area
follow-ups preserve the prior query semantics while changing only the grounded
scope. Referenced-set reads and writes require explicit referential grammar;
unqualified broad requests cannot silently borrow a prior set.

Set writes freeze the exact IDs before execution, re-read safe controllability,
use the registered `homeassistant.control` capability, and retain per-member
outcomes. The response reports requested, attempted, verified, failed and
unknown counts. Partial or unknown results never become a blanket success.
Safety-critical or unsupported domains cannot enter this path.

## Grounded measurements

`GroundedScalar` keeps a numeric or textual value attached to its semantic
type, display value, unit/currency, evidence status and redaction-safe
provenance. `CurrencyEvidence` records whether currency came from the exact
field, its local region, an unambiguous page/document declaration, or remained
unknown/ambiguous.

PDF extraction records ordinary text, layout text and safe pypdf text
fragments so a separately positioned currency glyph can remain evidence. Exact
field evidence has precedence; conflicting currencies remain ambiguous. User
location, account locale, provider and previous documents never verify a
currency. The structured scalar and provenance flow through document results,
derived results and durable `WorkingContext`.
