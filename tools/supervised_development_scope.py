"""Repository import for the single Core/protected-worker edit policy.

The protected installer installs the dependency-free implementation itself at
this module path, so production never imports mutable Core source.
"""

from bridge.app.development_scope import editable_path as editable_path
