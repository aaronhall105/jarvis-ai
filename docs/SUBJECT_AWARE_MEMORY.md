# Jarvis Subject-Aware Memory v14.1

## Purpose

Jarvis previously isolated every memory by the Home Assistant user who created
it. That protected privacy, but it also meant a fact Aaron saved about Amber was
not available when Amber asked from her own account.

v14.1 adds a subject and a visibility boundary to every memory.

## Visibility modes

- `private` — only the creator can read or remove it.
- `subject_and_owner` — the creator and the person the memory concerns can read
  it. The person concerned may also correct or remove it.
- `household` — available to authenticated Aaron and Amber accounts. Sensitive
  personal information is never permitted in this mode.

## Subject keys

- `aaron`
- `amber`
- `household`

## Safety rules

- Health, medical, allergies, intolerances, medications and similarly private
  details are marked `sensitive`.
- Sensitive memories cannot be household-wide.
- A private memory mentioning Amber does not become visible to Amber when the
  user explicitly says to keep it private.
- Unknown or anonymous Home Assistant users cannot read household memories.
- Direct REST memory endpoints require the generated
  `JARVIS_MEMORY_ADMIN_TOKEN`.
- Passwords, API keys, payment details and authentication secrets remain
  prohibited.

## Legacy migration

On first startup, the existing SQLite database is upgraded in place. A memory
whose stable subject clearly starts with `Amber` is tagged with `subject_key =
amber`. If Aaron created it, its visibility becomes `subject_and_owner`.

For example:

```text
Subject: Amber health conditions
Content: Amber is lactose intolerant.
```

becomes visible to both Aaron and Amber, while remaining unavailable to other
users.

The installer creates a consistent SQLite backup before migration.

## Natural examples

```text
Remember that Amber is lactose intolerant.
```

Jarvis stores it as sensitive, about Amber, visible to Aaron and Amber.

```text
Remember privately that Amber's birthday present is in the wardrobe.
```

Jarvis stores it as private to the person who said it.

```text
Remember for the household that bin day is Friday.
```

Jarvis stores it as a non-sensitive household memory.

## Verification

From Amber's Home Assistant account:

```text
Do I have any health conditions?
```

Expected response includes the permitted saved fact, such as lactose
intolerance, when that legacy or new memory exists.
