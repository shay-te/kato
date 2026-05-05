"""Hardened Docker sandbox for LLM-CLI agents.

Originally lived at ``kato_core_lib.sandbox`` and was extracted
into its own core-lib because sandboxing is a security product
boundary, not a kato-internal helper. The reusable mechanics
(image build/verify, command wrapping, workspace mount validation,
audit log shipping with hash chaining, TLS pinning, credential
pattern detection, untrusted-content delimiter framing) all live
here. Callers — kato today, possibly other agent-orchestration
tools later — decide *when* to use the sandbox; this library owns
*how* the sandbox actually contains the agent.

Public entry points are unchanged from the kato-internal era; see
:mod:`sandbox_core_lib.manager` for the spawn-path API and
:mod:`sandbox_core_lib.verify` for the startup preflight.
"""
