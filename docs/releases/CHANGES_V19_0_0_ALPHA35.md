# Jarvis 19.0.0-alpha35

This release makes Home Assistant questions set-aware and keeps document
measurements attached to their grounded units and currency evidence.

## Whole-home intelligence

- A structured query plan represents whole-home, exact-area, explicit-set and
  durable referenced-set scopes with category, predicate and aggregation.
- Home Assistant's registry and fresh state are the only sources of entity,
  device, area, capability and current-state truth. Multi-entity questions no
  longer collapse to one ranked name match.
- `off`, `unknown` and `unavailable` remain distinct. Responses use friendly
  names and retain the complete grounded set in WorkingContext.
- A reusable `HomeSnapshot` provides grounded presence, light, availability,
  media, appliance, battery, climate, lock and security views for concise house
  status answers. It does not enable proactive notifications.

## Conversational sets and actions

- Room follow-ups preserve the previous semantic operation while changing only
  the authoritative area scope.
- Referenced result sets survive Core restart through the existing durable,
  principal-scoped dialogue store.
- Exact-set writes freeze and revalidate every target, use the registered Home
  Assistant control capability with bounded concurrency, and report verified,
  failed and unknown member outcomes. Broad unreferenced commands cannot borrow
  a prior set implicitly.

## Grounded measurements

- `GroundedScalar` carries value, display value, semantic type, unit/currency,
  evidence status and provenance through derived results and WorkingContext.
- PDF extraction uses ordinary, layout and safe pypdf text-fragment evidence so
  separately positioned currency glyphs can be retained.
- Exact-field currency wins; unambiguous local, page or document declarations
  may ground a value; conflicts stay ambiguous. Location, locale, provider and
  document topic never prove currency.

## Compatibility

- Phone and Wear share versionCode `190370` and realtime protocol `2`.
- Alpha34's Android shell, Smart Inbox, persistent typed/IME keyboard behavior,
  endpoint failover, offline Task cache and realtime voice contracts are
  unchanged.
- Important-Only policy, authority, checkpoints and recoverable-mail semantics
  are unchanged. Permanent mailbox deletion remains unavailable.
