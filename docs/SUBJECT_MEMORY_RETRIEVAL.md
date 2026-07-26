# Subject-aware memory retrieval v14.1.1

This hotfix corrects semantic retrieval without changing memory permissions.

## Problem corrected

A permitted memory such as `Amber is lactose intolerant` was visible to Amber,
but a broad question such as `Do I have any health conditions?` contained no
literal words found in the stored fact. The original lexical search therefore
returned no match even though access control was correct.

## Retrieval model

The engine now combines:

- exact word matching;
- subject and owner ranking;
- visibility-aware ranking;
- bounded profile concepts for health, diet, birthday, work, contact details and
  preferences.

No external vector database or embedding service is used. Access filtering runs
before ranking, so concept matching cannot expose a private memory.

## Privacy invariants

- Private creator memories remain private.
- `subject_and_owner` memories remain visible only to the creator and subject.
- Household memories remain limited to configured household users.
- Concept matching never bypasses `_can_view` or `_can_edit`.
