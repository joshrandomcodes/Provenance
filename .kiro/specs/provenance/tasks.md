# Implementation Plan: Provenance

## Overview

Implement Provenance as a Python package with a thin Streamlit shell, pure domain services, explicit side-effect ports, bounded live-network adapters, and a transaction-safe local SQLite registry. Each prompt below builds on earlier work and leaves its code integrated through stable interfaces. Automated tests are placed beside the behavior they validate; optional property and integration test tasks may be skipped for a faster MVP, but the final release gate expects the complete suite.

## Tasks

- [x] 1. Establish the Python project and deterministic toolchain
  - [x] 1.1 Create the package skeleton and pin production dependencies
    - Create the `provenance` package boundaries from the design, the Streamlit entry point, package metadata, and one-shot application/test scripts.
    - Pin exact compatible versions of Python, Streamlit, Pillow, NumPy, requests, beautifulsoup4, and python-whois; disable telemetry, analytics, cloud persistence, remote logging, and environment-proxy inheritance by default.
    - Add configuration that keeps local Registry and runtime artifacts outside the source package and does not introduce production fixtures or synthetic evidence providers.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 17.1, 17.2_
  - [x] 1.2 Add pinned development and test tooling
    - Pin Hypothesis and the unit, integration, browser, accessibility, lint, and type-check tools needed by the design.
    - Configure one-shot test commands, temporary test data locations, strict type checking, and test markers that exclude opt-in live smoke checks from deterministic validation.
    - Add a startup/import smoke test proving the composition root cannot import test fixtures, fake clocks, mock resolvers, or synthetic evidence modules.
    - _Requirements: 1.2, 1.5, 1.6, 17.1, 17.2, 20.1-20.20_

- [x] 2. Implement typed domain foundations and Forge input validation
  - [x] 2.1 Create domain models, failures, clocks, and side-effect protocols
    - Implement immutable dataclasses, enums, value objects, typed `Result`/`Failure` values, UTC timestamp formatting, monotonic/UTC clock protocols, cancellation tokens, and Registry/network/draft/logging ports.
    - Represent every specified incident, extraction, scan, infrastructure, confirmation, and operation state without importing Streamlit or infrastructure libraries into domain modules.
    - _Requirements: 1.2, 6.12, 8.5, 10.1-10.4, 11.3, 14.3-14.5, 18.3, 18.7_
  - [x] 2.2 Implement metadata validation and bounded image decoding
    - Implement complete, accumulating validation for file size, decoded format/dimensions/pixels, Creator_ID, display name, contact email, postal address, rights statement, NUL characters, and Unicode code-point limits.
    - Implement Pillow verification/full-load behavior that produces C-contiguous eight-bit RGB plus an optional copied alpha channel, maps all failures to safe categories, and performs no hashing or mutation after rejection.
    - _Requirements: 2.1-2.5, 4.5, 17.4, 19.8_
  - [x]* 2.3 Write unit tests for typed models, clocks, validation, and image decoding
    - Cover exact byte/pixel boundaries, PNG/JPEG modes, truncated/corrupt images, decompression failures, all metadata predicates, accumulated errors, UTC second precision, and side-effect-free rejection.
    - Use dedicated tests for safe error details and preserved submitted values.
    - _Requirements: 2.1-2.5, 6.12, 17.6, 19.8_
  - [x]* 2.4 Write property test for payload creation time and identity
    - **Property 3: Payload creation uses exact validated identity and sampled UTC second**
    - Create `test_property_03_payload_creation.py` and use injected aware UTC clocks.
    - **Validates: Requirements 3.2, 6.12**
  - [x]* 2.5 Write property test for complete Forge validation
    - **Property 13: Forge validation is complete and side-effect free on rejection**
    - Create `test_property_13_forge_validation.py` and generate valid and invalid file/metadata combinations around every boundary.
    - **Validates: Requirements 2.1-2.5**

- [x] 3. Implement canonical image identity and the strict payload codec
  - [x] 3.1 Implement canonical source streaming and Asset_Hash computation
    - Serialize the exact source marker, unsigned 64-bit big-endian dimensions, and row-major RGB bytes into SHA-256 without including alpha, metadata, encoding, filename, or stride padding.
    - Validate array shape, dtype, range, dimensions, and row traversal before hashing.
    - _Requirements: 3.1, 20.1, 20.2_
  - [x] 3.2 Implement payload creation, canonical serialization, and strict parsing
    - Validate exact string fields and timestamp grammar/Gregorian validity; serialize canonical UTF-8 JSON with sorted keys and no insignificant whitespace.
    - Detect invalid UTF-8, multiple/non-object JSON, duplicate/wrong keys, non-string values, noncanonical bytes, and invalid fields without returning partial identity.
    - _Requirements: 3.2-3.8, 20.3, 20.4, 20.10_
  - [x]* 3.3 Write unit tests for canonical image and payload vectors
    - Cover known canonical byte/hash vectors, strided-array rejection or normalization policy, years 0001/9999, leap dates, second 59/60, duplicate keys, key order, whitespace, and invalid UTF-8.
    - _Requirements: 3.1-3.8_
  - [x]* 3.4 Write property test for canonical image identity equivalence
    - **Property 1: Canonical image identity equivalence**
    - Create `test_property_01_canonical_identity.py` with equivalent decoded pixels represented by varied encodings, metadata, alpha, and array layouts.
    - **Validates: Requirements 3.1, 20.1**
  - [x]* 3.5 Write property test for canonical-source collision retention
    - **Property 2: Canonical source difference collision retention**
    - Create `test_property_02_collision_retention.py`; retain and report any unequal generated canonical byte pair with equal SHA-256 values instead of assuming injectivity.
    - **Validates: Requirements 20.2**
  - [x]* 3.6 Write property test for canonical payload round trips
    - **Property 4: Canonical payload codec round trip**
    - Create `test_property_04_payload_roundtrip.py` and assert field equality plus byte-for-byte reserialization.
    - **Validates: Requirements 3.3, 3.4, 20.3, 20.4**
  - [x]* 3.7 Write property test for identity-safe invalid payload handling
    - **Property 5: Invalid payloads reveal no identity**
    - Create `test_property_05_invalid_payloads.py` with grammar-aware malformed byte strategies.
    - **Validates: Requirements 3.5, 3.6, 3.7, 20.10**
  - [x]* 3.8 Write property test for invalid serializer inputs
    - **Property 6: Invalid serializer input emits no bytes**
    - Create `test_property_06_invalid_serializer.py` and verify all applicable field issues are returned.
    - **Validates: Requirements 3.8**

- [x] 4. Implement the exact LSB watermark engine and lossless PNG boundary
  - [x] 4.1 Implement capacity, header, bit packing, embedding, and extraction
    - Implement the exact 13-byte header, MSB-first byte traversal, row-major RGB-channel traversal, least-significant-bit replacement, capacity checks, CRC-32 validation, and typed No_Watermark/Corrupt_Watermark/payload results.
    - Reject every recognized malformed structure without exposing identity fields or classifying a Registry match.
    - _Requirements: 4.1-4.5, 4.7-4.11_
  - [x] 4.2 Implement PNG encoding and round-trip verification
    - Convert embedded RGB and preserved alpha into an in-memory PNG, reopen it, and byte-compare dimensions, RGB, and alpha before exposing an artifact.
    - Return an exact failure and release incomplete bytes if encoding or verification fails.
    - _Requirements: 4.5, 4.6, 5.1, 5.5, 5.7_
  - [x]* 4.3 Write unit tests for watermark vectors and PNG round trips
    - Cover fewer than 32/104 channels, known header/CRC vectors, exact capacity/capacity+1, unsupported versions, impossible lengths, body-bit flips, untouched trailing channels, and alpha preservation.
    - _Requirements: 4.1-4.11, 5.5_
  - [x]* 4.4 Write property test for exact header encoding
    - **Property 7: Header encoding is exact**
    - Create `test_property_07_header_encoding.py`.
    - **Validates: Requirements 4.1**
  - [x]* 4.5 Write property test for watermark round trips
    - **Property 8: Watermark embedding and extraction round trip**
    - Create `test_property_08_watermark_roundtrip.py`.
    - **Validates: Requirements 4.2, 4.9, 20.5**
  - [x]* 4.6 Write property test for lossless embedding invariants
    - **Property 9: Embedding preserves the lossless image invariants**
    - Create `test_property_09_embedding_invariants.py`.
    - **Validates: Requirements 4.3-4.6, 20.6**
  - [x]* 4.7 Write property test for the exact capacity boundary
    - **Property 10: Capacity boundary is exact**
    - Create `test_property_10_capacity_boundary.py` and verify exact required/available counts and absence of effects on rejection.
    - **Validates: Requirements 4.7, 4.8, 20.7, 20.8**
  - [x]* 4.8 Write property test for recognized corruption
    - **Property 11: Recognized watermark corruption is never a match**
    - Create `test_property_11_recognized_corruption.py`.
    - **Validates: Requirements 4.11, 20.9**
  - [x]* 4.9 Write property test for missing magic
    - **Property 12: Missing magic is No_Watermark**
    - Create `test_property_12_missing_magic.py`.
    - **Validates: Requirements 4.10**

### Checkpoint - Ensure all foundational and domain tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Build the SQLite schema, migrations, integrity gate, and unit of work
  - [x] 5.1 Implement ordered checksum-verified migrations and schema constraints
    - Create strict tables, foreign keys, uniqueness/check constraints, binary URL identity, indexes, audit tombstones, operation receipts, and migration version/checksum records.
    - Keep all schema initialization local and deterministic.
    - _Requirements: 1.3, 6.1-6.6, 6.12, 10.5-10.7, 12.1, 12.6, 17.8, 18.7, 18.8_
  - [x] 5.2 Implement Registry connection startup and the write integrity gate
    - Enable and verify foreign keys per connection, configure durability/busy behavior, apply migrations transactionally, and run complete integrity and foreign-key checks before enabling writes.
    - Expose read-only diagnostics and reject every mutation when checks are pending, failed, or unavailable.
    - _Requirements: 6.9-6.11, 17.12, 18.11, 18.12_
  - [x] 5.3 Implement repositories and explicit SQLite unit-of-work ownership
    - Implement typed asset, incident, whitelist, audit, and operation repositories without internal commits.
    - Implement `BEGIN IMMEDIATE`, commit, rollback, sanitized failure mapping, and pre-commit transaction visibility guarantees.
    - _Requirements: 5.2-5.5, 6.7, 6.8, 11.8, 18.10-18.12_
  - [x]* 5.4 Write SQLite migration, integrity, isolation, and recovery tests
    - Test migration ordering/checksums, all constraints and indexes, foreign-key activation, failed integrity gates, second-connection visibility, rollback, and subprocess termination before and after commit.
    - _Requirements: 6.1-6.11, 17.11, 18.10-18.12_
  - [x]* 5.5 Write property test for failed transaction restoration
    - **Property 15: Failed transactions restore the exact prior registry state**
    - Create `test_property_15_transaction_rollback.py` with injected insertion, update, deletion, constraint, and commit failures.
    - **Validates: Requirements 5.5, 6.7, 6.8, 11.8, 17.11, 18.10, 20.16**

- [x] 6. Implement Registry business semantics and idempotent material actions
  - [x] 6.1 Implement creator-bound registration and reuse
    - Create/reuse exactly one Registered_Asset by Asset_Hash, preserve original metadata/timestamp on same-creator retries, and reject different-creator conflicts atomically.
    - _Requirements: 5.2-5.4, 6.4, 20.11_
  - [x] 6.2 Implement verified detection upsert and incident uniqueness
    - Cross-check both Asset_Hash and Creator_ID, create unique Detected/Fair Use incidents, and update only last-seen/latest context on rediscovery while preserving first-seen.
    - _Requirements: 6.1, 6.5, 10.1-10.7, 18.8_
  - [x] 6.3 Implement atomic status/whitelist/audit transitions and operation receipts
    - Revalidate transition plans inside one transaction, append exactly one Audit_Event, persist canonical operation receipts, and return prior outcomes for identical retries.
    - Include strike authorization, credit requested, mark/remove fair use, and explicit dispatch outcome command shapes.
    - _Requirements: 11.6-11.9, 12.1-12.8, 13.3, 16.6-16.8, 18.6-18.8, 18.10_
  - [x] 6.4 Implement compare-and-swap asset deletion
    - Build a count/fingerprint preview; under one transaction re-count, delete the asset and dependents only on an exact match, retain audit tombstones, and return a refreshed preview when stale.
    - _Requirements: 17.7-17.11_
  - [x]* 6.5 Write Registry business integration tests
    - Cover registration conflict/reuse, incident deduplication, exact whitelist scope, status transitions, one-audit semantics, operation retry receipts, stale deletion previews, cascades, and retained audit tombstones.
    - _Requirements: 5.2-5.5, 6.1-6.8, 10.1-10.7, 12.1-12.8, 17.7-17.11, 18.6-18.12_
  - [x]* 6.6 Write property test for creator-bound idempotent registration
    - **Property 14: Registration is creator-bound and idempotent**
    - Create `test_property_14_registration_idempotency.py`.
    - **Validates: Requirements 5.2-5.4, 6.4, 20.11**
  - [x]* 6.7 Write property test for confluent incident detection
    - **Property 16: Incident detection is confluent and unique**
    - Create `test_property_16_incident_confluence.py` and exercise permutations of zero through 100 detections.
    - **Validates: Requirements 6.5, 10.5, 10.6, 18.8, 20.12**
  - [x]* 6.8 Write property test for deletion compare-and-swap
    - **Property 38: Deletion confirmation is a compare-and-swap**
    - Create `test_property_38_deletion_cas.py`.
    - **Validates: Requirements 17.7, 17.8, 17.10**
  - [x]* 6.9 Write property test for atomic retry-idempotent actions
    - **Property 39: Confirmed material actions are atomic and retry-idempotent**
    - Create `test_property_39_material_actions.py`.
    - **Validates: Requirements 18.6, 18.7, 18.8**

- [x] 7. Assemble the Forge application workflow
  - [x] 7.1 Implement Forge preparation orchestration
    - Compose validation, bounded decode, canonical hashing, one-time UTC payload creation, serialization, capacity enforcement, embedding, and PNG round-trip verification into a volatile artifact.
    - Drop incomplete bytes on every failure and perform no Registry mutation during preparation.
    - _Requirements: 2.1-2.5, 3.1-3.8, 4.1-4.11, 5.5, 5.7_
  - [x] 7.2 Implement atomic Forge registration and download readiness
    - Register only a successfully encoded artifact, expose download only after matching create/reuse, sanitize the source stem, and return all required registration/capacity details.
    - Clear the artifact on conflict/failure and never expose an unregistered download.
    - _Requirements: 5.1-5.7, 17.2, 17.4_
  - [x]* 7.3 Write Forge service and Registry integration tests
    - Test success, reused records with changed submitted metadata, identity conflict, exact-capacity output, encoding failure, commit failure, rollback, artifact release, filename sanitization, and download gating.
    - _Requirements: 2.1-2.5, 3.1-3.8, 4.1-4.11, 5.1-5.7_

### Checkpoint - Ensure Registry and Forge tests pass (passed)
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement URL parsing, normalization, and public-address policy
  - [x] 8.1 Implement strict HTTP(S) URL values and normalization
    - Convert bare domains to HTTPS root URLs; reject credentials, malformed hosts, invalid percent escapes, unsupported schemes, and disallowed ports before DNS.
    - IDNA-normalize hosts, remove default ports/fragments, resolve dot segments, map empty paths to `/`, and preserve path case and query bytes.
    - Implement public unicast IP classification including IPv4-mapped IPv6 exclusions.
    - _Requirements: 7.1, 7.2, 7.6, 7.8, 9.2, 12.4, 12.5_
  - [x]* 8.2 Write unit tests for URL and address edge cases
    - Cover IDNA, IPv4/IPv6/mapped addresses, userinfo, effective ports, dot segments, fragments, path case, query bytes, malformed hosts, percent escapes, and every excluded address class.
    - _Requirements: 7.1-7.3, 7.6, 7.8, 9.2, 12.4, 12.5_
  - [x]* 8.3 Write property test for normalization idempotence
    - **Property 17: URL normalization is idempotent**
    - Create `test_property_17_url_normalization.py`.
    - **Validates: Requirements 7.1, 7.2, 9.2, 12.4, 20.13**

- [x] 9. Implement scan budgets, accounting, cancellation, and terminal summaries
  - [x] 9.1 Implement monotonic Scan_Budget and outcome reducers
    - Enforce every HTML/image/total byte, image count, pixel, redirect, connect, next-byte, and total elapsed limit with exact pre-retention accounting.
    - Model scheduling stop, outstanding cancellation, completed preservation, attempted terminal categories, skipped candidates, progress snapshots, and complete/incomplete summaries.
    - _Requirements: 8.4-8.12, 18.1, 18.3-18.5, 18.9_
  - [x]* 9.2 Write unit and state-machine tests for budget boundaries
    - Cover declared lengths, sentinel reads, arbitrary chunking, count/pixel boundaries, robots pause time, redirect attempts, cancellation timing, summary arithmetic, and one terminal category per attempted image.
    - _Requirements: 8.4-8.12, 18.1, 18.3-18.5, 18.9_
  - [x]* 9.3 Write property test that scan hard limits are never exceeded
    - **Property 20: Scan hard limits are never exceeded**
    - Create `test_property_20_scan_limits.py` with generated event sequences and monotonic clocks.
    - **Validates: Requirements 8.4, 8.5, 8.7-8.11, 20.17**
  - [x]* 9.4 Write property test for chunk-invariant byte accounting
    - **Property 21: Byte accounting is invariant under chunking**
    - Create `test_property_21_byte_accounting.py`.
    - **Validates: Requirements 8.6, 18.5**

- [ ] 10. Implement the pinned SSRF-safe HTTP, redirect, and robots stack
  - [x] 10.1 Implement immediate DNS resolution and pinned resolution values
    - Resolve immediately before every attempt, require a nonempty all-public A/AAAA set, share a single connection deadline across addresses, and expose no connection when policy fails.
    - _Requirements: 7.3, 7.6, 7.9, 8.12_
  - [x] 10.2 Implement the Requests-compatible pinned socket adapter
    - Connect only to validated pinned addresses; verify public/equal peer before request bytes and again after TLS wrapping; preserve hostname SNI/certificate checks.
    - Disable proxies, `.netrc`, retries, pooling, cookies, and automatic redirects; stream bodies under next-byte/total/cancellation deadlines and charge bytes exactly once before retention.
    - _Requirements: 7.3, 7.5-7.9, 8.4-8.7, 8.10-8.13, 17.2_
  - [x] 10.3 Implement independent redirect validation
    - Resolve each Location against the response URL, apply all URL/DNS/public/peer checks anew, enforce five redirects, safely account/discard redirect bodies, and avoid forwarding credentials across origins.
    - _Requirements: 7.4, 7.9, 8.4, 8.6_
  - [x] 10.4 Implement robots retrieval and explicit unavailable decision flow
    - Fetch origin `/robots.txt` with the same safe stack and user agent, enforce disallow before page access, and model unavailable continue/cancel controls while the scan deadline continues.
    - _Requirements: 8.1-8.3, 8.5, 8.11, 8.13_
  - [ ]* 10.5 Write network adapter, redirect, and robots contract tests
    - Use local scripted DNS/socket/HTTP/TLS fixtures to assert validation/resolve/connect/peer/TLS/request ordering and absence of request bytes on prohibited paths.
    - Cover rebinding, mixed addresses, mapped private peers, proxy environment variables, TLS failure, redirect loops/cross-origin behavior, slow connect/read, over-limit streams, cancellation, robots rules, and consistent user-agent/project URL.
    - _Requirements: 7.1-7.9, 8.1-8.13, 17.2, 18.2_
  - [ ]* 10.6 Write property test for unsafe destination rejection
    - **Property 18: Unsafe destinations cannot reach DNS or connection**
    - Create `test_property_18_unsafe_destinations.py` with call-tracing fakes at the port boundary.
    - **Validates: Requirements 7.3, 7.6, 7.8**
  - [ ]* 10.7 Write property test for independent redirect validation
    - **Property 19: Redirects receive independent complete validation**
    - Create `test_property_19_redirect_validation.py`.
    - **Validates: Requirements 7.4, 7.9**

- [ ] 11. Implement static image discovery, context extraction, and volatile evidence
  - [ ] 11.1 Implement bounded static HTML discovery and context extraction
    - Parse only bounded static HTML; visit `img` elements in document order and evaluate nonempty `src`, left-to-right `srcset`, then `data-src`.
    - Resolve, validate, normalize, and first-occurrence deduplicate at most 100 URLs; associate exact title, preceding heading, enclosing figcaption, alt text, and bounded ecommerce evidence with each origin element.
    - _Requirements: 9.1-9.3, 9.6, 18.4, 21.5_
  - [ ] 11.2 Implement in-memory image analysis and evidence lease lifecycle
    - Validate media type and pixel limits, fully decode in memory, extract one typed outcome, and release network bytes/arrays unless held by the single selected incident lease.
    - Ensure scan completion, cancellation, selection change, reset, and teardown revoke the appropriate leases without files, temp files, persistent caches, or diagnostic serialization.
    - _Requirements: 9.4, 9.5, 9.7, 17.3, 17.5, 17.6, 18.1_
  - [ ]* 11.3 Write discovery, context, image-analysis, and lifecycle tests
    - Cover malformed candidates/srcset, order/deduplication, exact context ownership, ecommerce evidence, media/decode/pixel failures, one failure per URL, and memory release events.
    - Assert no image bytes reach SQLite, files, temp directories, Streamlit caches, or logs.
    - _Requirements: 9.1-9.7, 17.3, 17.5, 17.6, 18.1_
  - [ ]* 11.4 Write property test for discovery order and uniqueness
    - **Property 22: Candidate discovery preserves specified order and uniqueness**
    - Create `test_property_22_candidate_discovery.py`.
    - **Validates: Requirements 9.1, 9.2**
  - [ ]* 11.5 Write property test for exact context association
    - **Property 23: Image context is associated exactly with its origin element**
    - Create `test_property_23_context_association.py`.
    - **Validates: Requirements 9.3, 9.6**

- [ ] 12. Implement scan orchestration, Registry cross-validation, and incident persistence
  - [ ] 12.1 Implement the user-initiated Scan service and scheduler
    - Require the session acknowledgement, perform robots/page discovery before image scheduling, coordinate one active worker/cancellation token/progress queue, and preserve completed results under per-image failure or terminal budget/cancel states.
    - Label live failures exactly, never substitute simulated evidence, and produce all required summary counts, bytes, elapsed time, static-HTML limitation text, and completion state.
    - _Requirements: 1.5, 1.6, 8.1-8.13, 18.1-18.5, 18.9, 21.1, 21.5, 21.6_
  - [ ] 12.2 Implement payload cross-validation and per-image incident commits
    - Query by extracted Asset_Hash, require Creator_ID equality, distinguish verified/unregistered/no/corrupt results, and commit one deduplicated incident only for completed verified images.
    - Apply exact whitelist scope and preserve previously committed incidents when later image work fails or the scan terminates.
    - _Requirements: 9.5, 10.1-10.7, 18.1, 18.2, 18.9, 20.20_
  - [ ]* 12.3 Write scan orchestration and failure-isolation integration tests
    - Cover page-level no-write failures, robots decisions, per-image continuation, cancellation races, completed-incident preservation, exact summary counts, Registry matching, whitelist suppression, and no production fallback evidence.
    - _Requirements: 1.5, 1.6, 8.1-8.13, 9.5, 10.1-10.7, 18.1-18.5, 18.9_
  - [ ]* 12.4 Write property test for terminal scan categories
    - **Property 24: Every attempted image has exactly one terminal category**
    - Create `test_property_24_scan_categories.py`.
    - **Validates: Requirements 9.5, 18.1, 18.3, 18.4, 18.9**
  - [ ]* 12.5 Write property test preventing incidents from nonverified results
    - **Property 25: Nonverified extraction cannot create an incident**
    - Create `test_property_25_nonverified_incidents.py`.
    - **Validates: Requirements 10.1-10.4, 20.20**

### Checkpoint - Ensure URL, network, discovery, and scan tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. Implement incident triage, fair-use, credit, and deletion application flows
  - [ ] 13.1 Implement incident evidence review and confirmation previews
    - Build active/fair-use views, selected-incident evidence models, side-by-side representation or labeled placeholders, available actions by status, legal-evidence labels, and before/after effect previews.
    - Keep cancellation/stale/failure paths on the selected evidence and expose retry without mutation.
    - _Requirements: 11.1-11.9, 21.4, 21.6_
  - [ ] 13.2 Implement exact-scope fair-use application commands
    - Validate rationale, preview upsert/removal effects, invoke atomic Registry plans, suppress exact matching incidents, and reopen only unresolved exact-scope Fair Use incidents as Detected on removal.
    - _Requirements: 12.1-12.8, 18.6-18.8_
  - [ ] 13.3 Implement credit request templates, confirmation, and user-controlled opening
    - Generate/edit/validate bounded templates with locked Asset_Hash, Creator_ID, and exact Page_URL; bind confirmation to exact content and commit Credit Requested plus one audit before separate delivery activation.
    - Label the workflow as attribution outreach rather than a legal notice and retain editable content on cancellation/failure without direct transmission.
    - _Requirements: 13.1-13.7, 21.6_
  - [ ] 13.4 Implement asset deletion preview and confirmation service
    - Present only the specified hash/count/retention effects, bind confirmation to the current compare-and-swap preview, and safely refresh or report failure without exposing sensitive values or local paths.
    - _Requirements: 17.7-17.12_
  - [ ]* 13.5 Write triage, fair-use, credit, and deletion integration tests
    - Cover evidence availability/placeholders, every action/cancel/retry path, rationale boundaries, exact URL scope variants, stale previews, credit edits/stale confirmation, communication-tool failures, and atomic audits.
    - _Requirements: 11.1-11.9, 12.1-12.8, 13.1-13.7, 17.7-17.12, 18.6-18.8_
  - [ ]* 13.6 Write property test for exact whitelist visibility
    - **Property 26: Exact whitelist scope partitions incident visibility**
    - Create `test_property_26_whitelist_visibility.py`.
    - **Validates: Requirements 10.7, 12.3-12.5, 20.14, 20.15**
  - [ ]* 13.7 Write property test for fair-use upsert and removal
    - **Property 27: Fair-use upsert and removal affect only the exact scope**
    - Create `test_property_27_fair_use_transitions.py`.
    - **Validates: Requirements 12.1, 12.2, 12.6-12.8**
  - [ ]* 13.8 Write property test for inert cancelled incident actions
    - **Property 28: Cancelled incident actions are observationally inert**
    - Create `test_property_28_cancelled_actions.py`.
    - **Validates: Requirements 11.7, 17.9**
  - [ ]* 13.9 Write property test for credit template locking and ranges
    - **Property 29: Credit templates preserve locked identity and validate all ranges**
    - Create `test_property_29_credit_templates.py`.
    - **Validates: Requirements 13.1, 13.6**
  - [ ]* 13.10 Write property test for failed/cancelled credit workflows
    - **Property 31: Failed or cancelled credit workflow preserves committed state**
    - Create `test_property_31_credit_failure_state.py`.
    - **Validates: Requirements 13.7**

- [ ] 14. Implement bounded live DNS and WHOIS infrastructure resolution
  - [ ] 14.1 Implement bounded live DNS evidence resolution
    - Apply Page_URL/public-address policy, one five-second monotonic lease, stable deduplication, address/name count and length bounds, independent returned/no-record/failure/timeout states, and UTC source timestamps.
    - _Requirements: 14.1, 14.3, 14.5, 14.8_
  - [ ] 14.2 Implement the bounded pinned WHOIS wire adapter and parser boundary
    - Retrieve TCP/43 data with pinned public peers, five-second connect, 15-second next-byte, 20-second aggregate, referral validation, and 1,048,576-byte aggregate limits.
    - Feed only bounded text to the pinned python-whois parser entry point; fail closed on incompatibility, release raw buffers, bound/deduplicate output, and rank valid `abuse` addresses first without inference.
    - _Requirements: 14.2-14.8, 17.6_
  - [ ] 14.3 Implement strike-investigation orchestration and recipient selection
    - Require the session infrastructure acknowledgement, run DNS and WHOIS with independent visible outcomes, label registrar/organization as candidates, and require selection or a valid labeled user-entered recipient before notice preparation.
    - Record strike authorization/audit atomically before live lookup and create no simulated provider/contact evidence.
    - _Requirements: 1.5, 1.6, 14.1-14.8, 18.6-18.8, 21.2, 21.4, 21.6_
  - [ ]* 14.4 Write DNS/WHOIS adapter and strike integration tests
    - Use scripted local DNS/socket/WHOIS fixtures for referrals, split/delayed/malformed/non-ASCII/oversized responses, peer mismatch, deadlines, parser incompatibility, output caps, ranking, raw-buffer release, and independent lookup outcomes.
    - _Requirements: 14.1-14.8, 17.6, 21.2_
  - [ ]* 14.5 Write property test for bounded non-inferred infrastructure output
    - **Property 32: Infrastructure output is bounded and never inferred**
    - Create `test_property_32_infrastructure_output.py`.
    - **Validates: Requirements 14.3-14.6**

- [ ] 15. Implement DMCA compilation, confirmations, local drafts, and dispatch auditing
  - [ ] 15.1 Implement strict credit/DMCA field validators and notice compilation
    - Validate all exact ranges, whitespace/NUL, email, telephone, subject/body, incident/whitelist/status, and locked evidence prerequisites.
    - Compile every statutory statement, exact incident/registration/watermark field, electronic signature, and required evidence/legal-assistance disclaimer without allowing locked evidence edits.
    - _Requirements: 15.1-15.4, 15.7, 15.8, 20.18_
  - [ ] 15.2 Implement canonical preview and attestation fingerprints
    - Fingerprint length-prefixed canonical UTF-8 fields for credit, notice, seven named attestations, exact-notice responsibility, and delivery readiness.
    - Regenerate previews and clear every dependent confirmation when any bound incident, recipient, subject, body, evidence, input, or signature changes.
    - _Requirements: 13.2, 15.5, 15.6, 15.9, 16.2, 21.3_
  - [ ] 15.3 Implement the local email-draft adapter
    - Build an exact percent-encoded `mailto:` draft for recipient, subject, and full body; enforce platform command limits and report unavailable/cancelled/open failures without temporary files, truncation, SMTP, or transmission.
    - _Requirements: 13.5, 13.7, 16.1-16.4, 16.9, 17.2_
  - [ ] 15.4 Implement dispatch readiness, explicit activation, and outcome auditing
    - Fail closed until every current prerequisite and confirmation is present, open only the displayed card on explicit activation, and require Sent/Not Sent/Cancel for the current attempt.
    - Record exactly one idempotent successful dispatch audit only for explicit Sent, storing recipient and notice hash but never the full notice body.
    - _Requirements: 15.5-15.9, 16.1-16.9, 18.6-18.8, 20.19, 21.3, 21.6_
  - [ ]* 15.5 Write notice, confirmation, draft, and dispatch integration tests
    - Cover every field boundary and missing prerequisite, immutable evidence, stale edits, seven independent attestations, readiness binding, exact draft projection, command-length failure, unavailable client, every explicit outcome, retry receipts, and absence of direct/background delivery.
    - _Requirements: 15.1-15.9, 16.1-16.9, 17.2, 17.6, 18.6-18.8_
  - [ ]* 15.6 Write property test for confirmation staleness
    - **Property 30: Confirmation fingerprints make every relevant edit stale**
    - Create `test_property_30_confirmation_fingerprints.py`.
    - **Validates: Requirements 13.2, 15.6, 15.9, 16.2, 21.3**
  - [ ]* 15.7 Write property test for complete valid notices
    - **Property 33: Valid DMCA inputs produce a complete locked notice**
    - Create `test_property_33_dmca_completeness.py`.
    - **Validates: Requirements 15.1-15.4, 20.18**
  - [ ]* 15.8 Write property test for fail-closed dispatch readiness
    - **Property 34: Dispatch readiness is complete and fail-closed**
    - Create `test_property_34_dispatch_readiness.py`.
    - **Validates: Requirements 15.5, 15.7, 15.8, 20.19**
  - [ ]* 15.9 Write property test for exact dispatch-card projection
    - **Property 35: Dispatch cards are exact projections**
    - Create `test_property_35_dispatch_projection.py`.
    - **Validates: Requirements 16.1, 16.3**
  - [ ]* 15.10 Write property test for explicit Sent semantics
    - **Property 36: Only explicit Sent records success**
    - Create `test_property_36_explicit_sent.py`.
    - **Validates: Requirements 16.5-16.9**

- [ ] 16. Build the Streamlit dashboard, session reducers, and accessible interaction layer
  - [ ] 16.1 Implement typed Streamlit session state and event reducers
    - Store one versioned SessionModel with operation nonces, forms, previews, fingerprints, acknowledgements, progress, focus target, one evidence lease, and volatile Forge output.
    - Ignore stale/duplicate callbacks, preserve inputs on validation failure, and permit at most one active scan and one strike investigation per session.
    - _Requirements: 1.1, 5.7, 8.3, 8.11, 13.2, 15.9, 19.3, 19.7-19.9, 21.1-21.3, 21.6_
  - [ ] 16.2 Implement The Forge tab
    - Render labeled inert metadata inputs, complete error summaries, progress/status, created/reused details, required output metrics, and the gated download from Forge services.
    - Never render user data as Markdown/HTML or expose output before matching registration.
    - _Requirements: 1.1, 2.6, 5.1, 5.6, 5.7, 19.1-19.3, 19.7, 19.8_
  - [ ] 16.3 Implement Web Radar tab
    - Render authorization acknowledgement, URL form, robots continue/cancel, live progress, cancellation, per-image outcomes, inert evidence, and complete/static-HTML-limited summaries.
    - Start network work only from the specific user control and keep Streamlit calls on the main thread.
    - _Requirements: 1.1, 8.3, 8.10, 8.11, 9.6, 9.8, 18.3, 19.1-19.4, 19.7-19.9, 21.1, 21.4-21.6_
  - [ ] 16.4 Implement Incident Triage tab and strike/dispatch views
    - Render active/fair-use lists, selected evidence/alt text/placeholders, action previews, rationales, credit flow, strike evidence, notice attestations, Dispatch Card, deletion preview, and explicit draft outcomes.
    - Keep all evidence/legal limitations visible at decision points and preserve retryable content after failures.
    - _Requirements: 1.1, 11.1-11.9, 12.3, 13.1-13.7, 14.3-14.7, 15.1-15.9, 16.1-16.9, 17.7-17.12, 19.1-19.9, 21.2-21.6_
  - [ ] 16.5 Implement the accessibility bridge and fail-closed compatibility probe
    - Use fixed local code only to provide stable focus movement/restoration, persistent labels, accessible names/descriptions, text status, alt text, keyboard interaction, aria-live updates, and supported-theme contrast.
    - Accept only enum focus targets and assign announcements with `textContent`; disable affected actions visibly if the pinned Streamlit DOM contract is incompatible.
    - _Requirements: 17.3, 17.4, 19.1-19.9_
  - [ ]* 16.6 Write Streamlit browser, security, and accessibility tests
    - Verify exactly three named tabs, keyboard-only operation/no traps, focus transitions/restoration, labels, alt text, textual states, one-second live updates, contrast, acknowledgement gates, stale confirmations, and user-activation-only actions.
    - Inject hostile strings and assert text-node rendering with no active HTML, Markdown, script, style, navigation, or remote-image loading.
    - _Requirements: 1.1, 2.6, 9.8, 17.3, 17.4, 19.1-19.9, 21.1-21.6_
  - [ ]* 16.7 Write property test for validation summaries and preserved input
    - **Property 40: Validation summaries are complete and preserve input**
    - Create `test_property_40_validation_summaries.py` against the UI model/reducer boundary.
    - **Validates: Requirements 19.3, 19.8**

- [ ] 17. Add safe rendering, redacted diagnostics, and privacy enforcement
  - [ ] 17.1 Implement inert rendering helpers and allowlist-based local diagnostics
    - Provide only text/literal-code rendering APIs with bounded values and no unsafe HTML/Markdown/remote media behavior.
    - Implement opt-in local logging with approved operation IDs, safe failure codes, bounded hosts, counts, durations, and opaque record IDs while denying sensitive payloads and local paths.
    - _Requirements: 2.6, 9.6, 9.8, 17.1-17.6, 17.11, 17.12_
  - [ ]* 17.2 Write privacy, rendering, and redaction tests
    - Generate image bytes, metadata, notice bodies, addresses, emails, URL queries, hostile markup, exception arguments, and paths; assert none enter diagnostics, active rendering, persistent caches, or unauthorized outbound requests.
    - _Requirements: 2.6, 9.8, 17.1-17.6, 17.11, 17.12_
  - [ ]* 17.3 Write property test excluding sensitive diagnostics
    - **Property 37: Sensitive values never enter diagnostic records**
    - Create `test_property_37_diagnostic_redaction.py`.
    - **Validates: Requirements 17.6, 17.11**

### Checkpoint - Ensure triage, infrastructure, notice, UI, and privacy tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 18. Wire the complete application and enforce release validation
  - [ ] 18.1 Implement the production composition root
    - Wire real clocks, SQLite, safe HTTP/DNS/WHOIS, local draft, redacted logging, application services, evidence buffer, session facade, and all three Streamlit tabs.
    - Fail visibly when Registry integrity, parser capability, accessibility compatibility, or required local dependencies are unavailable; include no production mock, synthetic evidence, cloud service, direct sender, or automatic action path.
    - _Requirements: 1.1-1.6, 6.9-6.11, 14.5, 16.4, 17.1-17.6, 19.1-19.9, 21.6_
  - [ ]* 18.2 Write full-stack local integration tests
    - Exercise Forge create/reuse/download, acknowledged static scan with local protocol fixtures, verified incident creation/deduplication, triage/whitelist/credit/strike flows, notice confirmation, local draft outcomes, deletion, crash recovery, and resource cleanup.
    - Assert all external-looking data comes from the scripted live adapter boundary, no image/notice secret persists, and every required audit/rollback behavior holds.
    - _Requirements: 1.1-1.6, 5.1-5.7, 6.1-6.12, 8.1-8.13, 9.1-9.8, 10.1-10.7, 11.1-11.9, 12.1-12.8, 13.1-13.7, 14.1-14.8, 15.1-15.9, 16.1-16.9, 17.1-17.12, 18.1-18.12, 19.1-19.9, 21.1-21.6_
  - [ ] 18.3 Add the one-shot release validation gate
    - Add a deterministic command that runs formatting/lint, strict type checking, unit tests, all 40 property tests with the configured example minimum, SQLite/network/integration suites, browser accessibility/security tests, migration checksum checks, and a package/import smoke build.
    - Keep opt-in user-approved public network smoke checks separate; make deterministic failures visible and never replace failed live compatibility with fixtures in production.
    - _Requirements: 1.5, 1.6, 6.9-6.11, 14.5, 17.1-17.6, 19.1-19.9, 20.1-20.20_

### Final checkpoint - Ensure the complete release gate passes
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP; they are still included in dependency scheduling and are required by the full release gate.
- Every numbered correctness property from the approved design has one dedicated optional property-test task and test module.
- Tests use local deterministic adapters and temporary Registries. Public-network compatibility smoke checks remain opt-in and user-authorized.
- Implementation tasks use Python throughout, as selected by the approved design.
- Checkpoints are validation pauses and are intentionally excluded from the dependency graph.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["2.2", "3.1", "5.1", "8.1", "9.1"] },
    { "id": 3, "tasks": ["3.2", "5.2", "8.2", "9.2", "17.1"] },
    { "id": 4, "tasks": ["2.3", "2.4", "2.5", "3.3", "3.4", "3.5", "3.6", "3.7", "3.8", "4.1", "5.3", "8.3", "9.3", "9.4", "10.1", "11.1", "17.2"] },
    { "id": 5, "tasks": ["4.2", "4.4", "5.4", "5.5", "6.1", "10.2", "11.2", "14.1", "15.1", "17.3"] },
    { "id": 6, "tasks": ["4.3", "4.5", "4.6", "4.7", "4.8", "4.9", "6.2", "6.6", "10.3", "11.3", "14.2", "15.2"] },
    { "id": 7, "tasks": ["6.3", "6.7", "7.1", "10.4", "10.6", "10.7", "11.4", "11.5", "15.6"] },
    { "id": 8, "tasks": ["6.4", "6.9", "7.2", "10.5", "12.1", "13.1", "14.3", "14.5", "15.3", "15.7"] },
    { "id": 9, "tasks": ["6.8", "7.3", "12.2", "13.2", "13.3", "13.8", "14.4", "15.4"] },
    { "id": 10, "tasks": ["6.5", "12.3", "12.4", "12.5", "13.4", "13.6", "13.7", "13.9", "13.10", "15.5", "15.8", "15.9", "15.10"] },
    { "id": 11, "tasks": ["13.5", "16.1"] },
    { "id": 12, "tasks": ["16.2", "16.3", "16.4"] },
    { "id": 13, "tasks": ["16.5"] },
    { "id": 14, "tasks": ["16.6", "16.7"] },
    { "id": 15, "tasks": ["18.1"] },
    { "id": 16, "tasks": ["18.2"] },
    { "id": 17, "tasks": ["18.3"] }
  ]
}
```
