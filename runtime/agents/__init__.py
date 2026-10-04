"""NEXUS agent package — concrete agent implementations for multi-agent workflows.

Each agent is a real execution boundary with distinct capabilities and
execution strategies. Agents consume input artifacts, perform real operations
(possibly using BoundedAgentRuntime for observability), and produce output
artifacts with full provenance.

Truth boundary:
- OBSERVED: facts verified by real runtime observation (e.g. filesystem reads)
- INFERRED: model or rule-based reasoning output (always untrusted)
- VERIFIED: independent deterministic validation of artifacts
"""
