# Requirements Document

## Introduction

Provenance is an open-source, local-first digital rights management and copyright protection application for creators. The application uses a Python and Streamlit dashboard with three tabs: The Forge, Web Radar, and Incident Triage. The Forge creates lossless watermarked images and local registrations; Web Radar performs user-initiated live scans of public web pages for registered watermarks; Incident Triage supports evidence review, fair-use handling, credit outreach, and user-controlled copyright notice preparation. Network-dependent functions use live responses and expose failures rather than substituting simulated data. Provenance assists evidence collection and notice preparation but does not determine ownership, infringement, fair use, provider liability, or legal validity.

## Glossary

- **Provenance_System**: The complete local Python and Streamlit application specified by this document.
- **Dashboard**: The Streamlit user interface containing The Forge, Web Radar, and Incident Triage tabs.
- **The_Forge**: The Dashboard tab that validates an uploaded image and Creator_Metadata, creates a Registered_Asset, embeds a Watermark_Payload, and provides a Watermarked_Image for download.
- **Web_Radar**: The Dashboard tab that performs a user-initiated live Scan of a public Page_URL.
- **Incident_Triage**: The Dashboard tab that displays evidence and records a creator-selected Incident_Status.
- **Strike_Engine**: The Provenance_System component that performs live infrastructure resolution and prepares a DMCA_Notice and Dispatch_Card.
- **Registry**: The local SQLite database that stores Registered_Assets, Incidents, Whitelist_Entries, and Audit_Events.
- **Creator_Metadata**: User-provided data consisting of a Creator_ID, display name, optional contact email, optional postal address, and optional rights statement.
- **Creator_ID**: A creator-selected identifier containing 1 through 64 ASCII letters, digits, periods, underscores, or hyphens.
- **Source_Image**: A successfully decoded PNG, JPG, or JPEG input before watermark embedding.
- **Canonical_Source**: The byte sequence `PRVN-SOURCE\x00 || width_u64_be || height_u64_be || rgb_bytes`, where dimensions are unsigned 64-bit big-endian values and `rgb_bytes` are eight-bit RGB channels in row-major pixel order.
- **Asset_Hash**: The 64-character lowercase hexadecimal SHA-256 digest of a Canonical_Source.
- **Watermark_Payload**: Canonical UTF-8 JSON containing exactly `asset_hash`, `creator_id`, and `created_at`, with lexicographically sorted keys, no insignificant whitespace, and a UTC timestamp in `YYYY-MM-DDTHH:MM:SSZ` format.
- **Payload_Serializer**: The component that converts Watermark_Payload fields to canonical UTF-8 JSON bytes.
- **Payload_Parser**: The component that validates and converts canonical Watermark_Payload bytes to fields.
- **Watermark_Header**: The 13-byte sequence consisting of the ASCII Magic_Marker `PRVN`, one Schema_Version byte, a four-byte unsigned big-endian payload length, and a four-byte unsigned big-endian CRC-32 of the Watermark_Payload bytes.
- **Magic_Marker**: The four ASCII bytes `PRVN` at the start of a Watermark_Header.
- **Schema_Version**: The one-byte watermark schema identifier; this specification defines version `1`.
- **LSB_Steganography**: Storage of one payload bit in the least significant bit of each RGB channel.
- **Watermark_Engine**: The component that embeds and extracts a Watermark_Header and Watermark_Payload by LSB_Steganography.
- **Embedding_Order**: RGB channels traversed left-to-right and top-to-bottom, with each Watermark_Header and Watermark_Payload byte traversed most-significant bit first.
- **Watermark_Capacity**: `max(0, floor((width × height × 3) / 8) - 13)` payload bytes for a decoded image of `width` by `height` pixels; an image with fewer than 13 available bytes cannot contain the Watermark_Header.
- **Watermarked_Image**: The PNG output produced by embedding a Watermark_Payload into the RGB channels of a Source_Image while preserving any alpha channel.
- **Registered_Asset**: One Registry record identified by Asset_Hash and containing Creator_ID, registration timestamp, Source_Image dimensions, source media type, and user-consented Creator_Metadata.
- **Page_URL**: The absolute HTTP or HTTPS URL submitted to Web_Radar or produced from a submitted domain by using the HTTPS scheme and root path.
- **Image_URL**: An absolute HTTP or HTTPS URL resolved from an HTML `img` element.
- **Normalized_URL**: A URL with a lowercase scheme and IDNA-normalized lowercase host, no default port, a `/` empty path, resolved dot segments, no fragment, and otherwise unchanged path case and query bytes.
- **Public_Network_Address**: A unicast IP address that is not loopback, private, link-local, multicast, unspecified, reserved, or an IPv4-mapped representation of any excluded address.
- **SSRF**: Server-side request forgery, in which user-influenced network access reaches an unintended local or restricted resource.
- **Scan**: One bounded, live retrieval and analysis of a Page_URL and hosted images initiated by a user.
- **Scan_Budget**: A maximum of 2 MiB of HTML, 100 unique Image_URL values, 10 MiB per compressed image, 40 megapixels per decoded image, 50 MiB total response bytes, five redirects per request, a five-second connection timeout, a 15-second read timeout, and 120 seconds elapsed time per Scan.
- **Page_Context**: The document title, nearest preceding `h1` through `h6` text, enclosing `figcaption` text, and `alt` text associated with an HTML `img` element.
- **Ecommerce_Indicator**: Displayed evidence of a currency amount or code, `price`, `add to cart`, `buy now`, or Schema.org `Product` or `Offer` markup in Page_Context or the containing HTML element.
- **Incident**: A Registry record linking a Registered_Asset to a Normalized_URL Page_URL, Normalized_URL Image_URL, extraction evidence, Page_Context, discovery timestamps, and Incident_Status.
- **Incident_Status**: One of `Detected`, `Strike Authorized`, `Fair Use`, or `Credit Requested`.
- **Whitelist_Entry**: A local record scoped to one Asset_Hash and one exact Normalized_URL Page_URL, containing a rationale, creation timestamp, modification timestamp, and optional related Incident identifier.
- **Infrastructure_Evidence**: Live DNS and WHOIS results for a Page_URL host, including resolved public addresses, registrar or organization fields, and available abuse contact fields.
- **DMCA_Notice**: A user-reviewable notice template containing the elements identified in 17 U.S.C. §512(c)(3), cryptographic registration evidence, and user attestations.
- **Dispatch_Card**: A local preview of a notice recipient, subject, and body with controls that require informed user confirmation before opening the user’s email client.
- **Audit_Event**: A timestamped local record of a material user action or state transition without stored image bytes, contact secrets, or full notice bodies.
- **Active_Incident**: An Incident with `Detected`, `Strike Authorized`, or `Credit Requested` status that is not suppressed by a Whitelist_Entry.
- **Corrupt_Watermark**: An extraction result for a recognized or partially recognized Watermark_Header that has an unsupported Schema_Version, impossible length, CRC-32 mismatch, noncanonical JSON, invalid field, or invalid timestamp.
- **No_Watermark**: An extraction result in which the Magic_Marker is absent.
- **Verified_Match**: An extraction result whose Watermark_Payload is valid and whose Asset_Hash and Creator_ID match one Registered_Asset.

## Requirements

### Requirement 1: Local-First Application Architecture

**User Story:** As a creator, I want a self-contained local application, so that I can manage copyright evidence without sending private assets to an application service.

#### Acceptance Criteria

1. THE Provenance_System SHALL provide The_Forge, Web_Radar, and Incident_Triage as named Dashboard tabs.
2. THE Provenance_System SHALL execute application logic in Python using Streamlit, Pillow, NumPy, requests, beautifulsoup4, python-whois, and Python standard-library modules including sqlite3.
3. THE Provenance_System SHALL store Registered_Assets, Incidents, Whitelist_Entries, and Audit_Events only in the local Registry.
4. THE Provenance_System SHALL perform all Source_Image processing, Watermarked_Image processing, and evidence analysis on the local machine.
5. WHEN a user initiates a Scan or live infrastructure resolution, THE Provenance_System SHALL derive network-dependent results only from live responses received during that operation and label the operation with its actual completion or failure state.
6. IF a live response required by a Scan or live infrastructure resolution is unavailable, THEN THE Provenance_System SHALL identify the unavailable response and affected operation as failed and create no simulated evidence, provider data, abuse contacts, matches, or Incidents.

### Requirement 2: Source Image and Creator Metadata Validation

**User Story:** As a creator, I want invalid or unsafe Forge input rejected explicitly, so that registrations represent well-formed source material.

#### Acceptance Criteria

1. WHEN a user submits a file containing 1 through 26,214,400 bytes whose decoded format is PNG or JPEG, whose decoded width and height are each at least 1 pixel, whose decoded width multiplied by decoded height is at most 40,000,000 pixels, and whose pixels Pillow can load completely, THE The_Forge SHALL create an eight-bit RGB representation containing exactly three bytes per pixel for hashing and embedding.
2. IF an uploaded file contains 0 bytes, contains 26,214,401 or more bytes, has an identifiable decoded format other than PNG or JPEG, has a decoded width or height below 1 pixel, has a decoded width multiplied by decoded height above 40,000,000 pixels, or cannot be fully decoded, THEN THE The_Forge SHALL reject the upload, report each applicable category as empty file, byte limit exceeded, unsupported format, invalid dimensions, pixel limit exceeded, or decode failure, perform no hashing or embedding, create no Registry record, and leave every existing Registry record and value unchanged.
3. WHEN a user submits Creator_Metadata, THE The_Forge SHALL require a Creator_ID satisfying the Glossary definition, a display name containing 1 through 200 Unicode code points, an absent contact email or a contact email containing 3 through 254 Unicode code points with exactly one `@` separator and nonempty local and domain parts, and a postal address and rights statement each containing at most 500 Unicode code points.
4. IF a present contact email contains fewer than 3 or more than 254 Unicode code points or lacks exactly one `@` separator with nonempty local and domain parts, THEN THE The_Forge SHALL identify the contact email field and every applicable invalid-length or invalid-format failure, reject the submission before hashing or embedding, create no Registry record, and leave every existing Registry record and value unchanged.
5. IF the Creator_ID does not satisfy the Glossary definition, any Creator_Metadata field contains a NUL code point, the display name contains fewer than 1 or more than 200 Unicode code points, the postal address contains more than 500 Unicode code points, or the rights statement contains more than 500 Unicode code points, THEN THE The_Forge SHALL identify every invalid field and applicable failure category, reject the submission before hashing or embedding, create no Registry record, and leave every existing Registry record and value unchanged.
6. WHEN The_Forge displays uploaded Creator_Metadata, THE Provenance_System SHALL render every value as inert text rather than executable HTML, Markdown, script, style, or active content.

### Requirement 3: Deterministic Asset Identity and Payload Codec

**User Story:** As a creator, I want a deterministic cryptographic identity embedded in each protected image, so that extracted evidence can be checked against my local registration.

#### Acceptance Criteria

1. WHEN The_Forge accepts a Source_Image, THE The_Forge SHALL compute the Asset_Hash as SHA-256 over the Canonical_Source.
2. WHEN The_Forge creates a Watermark_Payload, THE The_Forge SHALL set `asset_hash` to the computed Asset_Hash, `creator_id` to the validated Creator_ID, and `created_at` to the current UTC date and time sampled at payload creation, truncated to whole seconds, and formatted as `YYYY-MM-DDTHH:MM:SSZ`.
3. WHEN the Payload_Serializer receives exactly the valid string fields `asset_hash`, `creator_id`, and `created_at`, THE Payload_Serializer SHALL emit canonical UTF-8 JSON with member names in lexicographic byte order `asset_hash`, `created_at`, and `creator_id` and with no insignificant whitespace.
4. WHEN the Payload_Parser receives bytes that are byte-for-byte identical to the canonical bytes emitted by the Payload_Serializer for represented valid fields, THE Payload_Parser SHALL return the represented Asset_Hash, Creator_ID, and `created_at` value without changing any field value.
5. IF payload bytes are invalid UTF-8, are not exactly one JSON object, contain duplicate member names, contain a member-name set other than exactly `asset_hash`, `creator_id`, and `created_at`, contain a non-string member value, or are not byte-for-byte canonical for the represented fields, THEN THE Payload_Parser SHALL return Corrupt_Watermark without returning identity or timestamp fields.
6. IF `asset_hash` is not exactly 64 lowercase hexadecimal characters or `creator_id` does not satisfy the Creator_ID definition, THEN THE Payload_Parser SHALL return Corrupt_Watermark without returning identity or timestamp fields.
7. IF `created_at` is not exactly 20 ASCII characters in `YYYY-MM-DDTHH:MM:SSZ` format, has a year outside `0001` through `9999`, has a month outside `01` through `12`, has a day invalid for its Gregorian month and leap year, has an hour outside `00` through `23`, or has a minute or second outside `00` through `59`, THEN THE Payload_Parser SHALL return Corrupt_Watermark without returning identity or timestamp fields.
8. IF the Payload_Serializer receives an invalid field set, non-string field, invalid Asset_Hash, invalid Creator_ID, or invalid `created_at` value, THEN THE Payload_Serializer SHALL identify every invalid field and emit no Watermark_Payload bytes.

### Requirement 4: Exact Watermark Embedding and Extraction

**User Story:** As a creator, I want deterministic and lossless watermark behavior, so that capacity and extraction results are predictable.

#### Acceptance Criteria

1. WHEN the serialized Watermark_Payload byte length is at most Watermark_Capacity, THE Watermark_Engine SHALL construct the 13-byte Watermark_Header as `Magic_Marker || Schema_Version || payload_length_u32_be || payload_crc32_u32_be` using Schema_Version `1`, the exact payload byte count, and CRC-32 over the exact serialized payload bytes.
2. WHEN the Watermark_Engine constructs a Watermark_Header under criterion 1, THE Watermark_Engine SHALL embed the complete Watermark_Header followed immediately by the exact serialized Watermark_Payload bytes according to Embedding_Order.
3. WHEN embedding consumes an RGB channel, THE Watermark_Engine SHALL produce an RGB channel whose least significant bit equals the corresponding embedded bit and whose seven more-significant bits equal the original channel’s seven more-significant bits.
4. WHEN embedding completes, THE Watermark_Engine SHALL preserve the original value of every RGB channel after the final consumed channel.
5. WHEN a Source_Image contains an alpha channel, THE Watermark_Engine SHALL preserve every alpha-channel value at the original pixel position in the Watermarked_Image.
6. WHEN embedding succeeds, THE The_Forge SHALL encode the Watermarked_Image as PNG such that decoding reproduces the post-embedding dimensions, RGB values, and preserved alpha values exactly.
7. WHEN the serialized Watermark_Payload byte length equals Watermark_Capacity, THE Watermark_Engine SHALL complete embedding and produce a Watermarked_Image.
8. IF the serialized Watermark_Payload byte length exceeds Watermark_Capacity by one or more bytes, THEN THE The_Forge SHALL report the exact required and available payload byte counts, produce no Watermarked_Image, and leave the Registry unchanged.
9. WHEN extraction according to Embedding_Order reconstructs a complete Watermark_Header with Magic_Marker, Schema_Version `1`, a declared payload length at most Watermark_Capacity, the declared number of payload bytes, a matching CRC-32, and a valid Watermark_Payload, THE Watermark_Engine SHALL return the parsed Watermark_Payload.
10. IF an image contains fewer than 32 RGB channels or the first four bytes reconstructed according to Embedding_Order do not equal Magic_Marker, THEN THE Watermark_Engine SHALL return No_Watermark.
11. IF extraction according to Embedding_Order reconstructs Magic_Marker and then encounters fewer than 104 available RGB channels, an unsupported Schema_Version, a declared payload length above Watermark_Capacity, fewer available payload bytes than the declared length, a CRC-32 mismatch, noncanonical JSON, an invalid field, or an invalid timestamp, THEN THE Watermark_Engine SHALL return Corrupt_Watermark without returning identity or timestamp fields or reporting a Verified_Match.

### Requirement 5: Forge Registration and Download

**User Story:** As a creator, I want watermark creation and registration to succeed atomically, so that each downloadable asset has matching local evidence.

#### Acceptance Criteria

1. WHEN Watermarked_Image PNG encoding succeeds and Registry registration returns either a newly committed Registered_Asset or an unchanged existing Registered_Asset whose Asset_Hash and Creator_ID equal the encoded Watermark_Payload fields, THE The_Forge SHALL make that encoded PNG available for download with the Source_Image stem and `.provenance.png` suffix.
2. WHEN The_Forge requests registration after successful PNG encoding for an Asset_Hash absent from the Registry, THE Registry SHALL commit exactly one Registered_Asset in one transaction containing the Asset_Hash, Creator_ID, UTC registration timestamp, Source_Image dimensions, source media type, and exactly the user-approved Creator_Metadata.
3. WHEN a registration attempt supplies an Asset_Hash and Creator_ID equal to an existing Registered_Asset, THE Registry SHALL return that Registered_Asset as reused without creating, updating, or deleting a Registry record and preserve its original registration timestamp and Creator_Metadata regardless of submitted metadata differences.
4. IF the Registry contains a Registered_Asset with the submitted Asset_Hash and a different Creator_ID, THEN THE Registry SHALL report an identity conflict, preserve the existing Registered_Asset, create no new record, and keep the Watermarked_Image unavailable for download.
5. IF Watermarked_Image PNG encoding or Registry registration fails, THEN THE The_Forge SHALL identify the failed operation, discard the incomplete result, keep the Watermarked_Image unavailable for download, roll back every Registry change from the registration attempt, and restore the Registry state that existed immediately before the attempt.
6. WHEN registration succeeds by creating or reusing a Registered_Asset, THE The_Forge SHALL display whether the record was created or reused, the Asset_Hash, Creator_ID, stored registration timestamp, output dimensions, serialized payload byte length, and Watermark_Capacity.
7. WHILE PNG encoding has not succeeded or Registry registration has not returned a matching newly committed or unchanged existing Registered_Asset, THE The_Forge SHALL keep the Watermarked_Image unavailable for download.

### Requirement 6: Registry Integrity and Recovery

**User Story:** As a creator, I want durable and internally consistent evidence records, so that later incident decisions reference valid registrations.

#### Acceptance Criteria

1. THE Registry SHALL ensure that every Incident references exactly one existing Registered_Asset by Asset_Hash.
2. THE Registry SHALL ensure that every Whitelist_Entry references exactly one existing Registered_Asset by Asset_Hash.
3. THE Registry SHALL ensure that each Whitelist_Entry containing a related Incident identifier references exactly one existing Incident.
4. THE Registry SHALL contain at most one Registered_Asset for each Asset_Hash.
5. THE Registry SHALL contain at most one Incident for each `(Asset_Hash, Normalized_URL Page_URL, Normalized_URL Image_URL)` combination.
6. THE Registry SHALL contain at most one Whitelist_Entry for each `(Asset_Hash, exact Normalized_URL Page_URL)` combination.
7. WHEN one requested state change affects two or more Registry records, THE Registry SHALL apply every affected change in one SQLite transaction and expose none of the changes before commit.
8. IF an insertion, update, deletion, constraint check, or commit fails or the requested state change would violate a foreign-key or uniqueness criterion, THEN THE Registry SHALL reject the entire requested state change, identify the failed operation and failure category, roll back every transaction change, and leave the Registry in its pre-transaction state.
9. WHEN the Provenance_System opens the Registry, THE Registry SHALL complete a SQLite integrity check and foreign-key consistency check before accepting an insertion, update, or deletion and enable writes only when both checks report zero errors or violations.
10. IF an opening integrity check reports one or more errors, an opening foreign-key consistency check reports one or more violations, or either check cannot complete, THEN THE Provenance_System SHALL disable Registry writes for the application session and display the Registry path with guidance to back up the failed Registry before restoring a known-good backup or creating a new Registry.
11. WHILE opening checks have not completed successfully or Registry writes are disabled, THE Registry SHALL reject every insertion, update, and deletion, identify whether checks are pending or failed, and leave the Registry unchanged.
12. THE Registry SHALL store timestamps as UTC values in `YYYY-MM-DDTHH:MM:SSZ` format with whole-second precision.

### Requirement 7: SSRF-Safe URL Acceptance

**User Story:** As an operator, I want web scans constrained to public destinations, so that submitted URLs cannot access local or restricted services.

#### Acceptance Criteria

1. WHEN a user submits a domain without a scheme, THE Web_Radar SHALL construct a Page_URL using HTTPS and `/` as the path.
2. WHEN a user submits a Page_URL, THE Web_Radar SHALL accept only absolute HTTP or HTTPS URLs on port 80 or 443 without embedded credentials.
3. WHEN Web_Radar prepares any outbound connection attempt, THE Web_Radar SHALL resolve the destination host immediately before the attempt and proceed only when resolution returns at least one A or AAAA address and every returned address is a Public_Network_Address.
4. WHEN a redirect response specifies a destination, THE Web_Radar SHALL resolve the destination against the response URL to produce an absolute URL and apply the scheme, effective-port, credential, host, DNS, and Public_Network_Address checks before opening the redirected connection.
5. WHEN an outbound connection is established, THE Web_Radar SHALL verify before transmitting a request byte or accepting a response byte that the connected peer is a Public_Network_Address and equals an address returned by the immediately preceding DNS resolution.
6. IF DNS resolution fails, returns no A or AAAA address, or returns any address that is not a Public_Network_Address, THEN THE Web_Radar SHALL stop the affected request before opening the connection and report an SSRF protection error.
7. IF a connected peer is not a Public_Network_Address or differs from every address returned by the immediately preceding DNS resolution, THEN THE Web_Radar SHALL close the connection before transmitting a request byte or accepting a response byte and report an SSRF protection error.
8. IF any destination URL contains an unsupported scheme, disallowed effective port, malformed host, or embedded credentials, THEN THE Web_Radar SHALL reject that destination before DNS resolution or other network access.
9. WHEN Web_Radar prepares each connection attempt for Page_URL, robots.txt, a redirect destination, or Image_URL, THE Web_Radar SHALL independently apply URL checks before DNS resolution, DNS and Public_Network_Address checks before connection, and connected-peer verification immediately after connection establishment.

### Requirement 8: Responsible and Resource-Bounded Crawling

**User Story:** As an operator, I want live scans to respect site rules and local resource limits, so that scanning remains controlled and reliable.

#### Acceptance Criteria

1. WHEN a Scan starts, THE Web_Radar SHALL retrieve and evaluate the destination origin’s robots.txt rules for the Provenance user agent before issuing the Page_URL request.
2. IF robots.txt disallows the Page_URL for the Provenance user agent, THEN THE Web_Radar SHALL stop the Scan, issue no Page_URL or Image_URL request, and report the applicable rule.
3. IF robots.txt is unavailable because of a network or server error, THEN THE Web_Radar SHALL pause the Scan, identify the failure, provide continue and cancel controls requiring an explicit selection for that Scan, and issue no Page_URL or Image_URL request unless the user selects continue for that Scan.
4. WHILE a Scan is active, THE Web_Radar SHALL enforce at most 2,097,152 HTML response bytes, 100 unique Image_URL values, 10,485,760 compressed response bytes per image, 40,000,000 decoded pixels per image, 52,428,800 total response-body bytes, five redirects per request, five seconds per connection attempt, 15 seconds awaiting each next response byte, and 120 seconds elapsed from Scan start.
5. WHILE a Scan is active, THE Web_Radar SHALL measure connection, next-byte, and total Scan durations with a monotonic clock, restart only the 15-second next-byte interval when a response byte arrives, and avoid restarting or extending the 120-second interval for a redirect, request, response byte, robots-confirmation pause, or wall-clock adjustment.
6. WHILE a Scan is active, THE Web_Radar SHALL count each streamed response-body byte received for robots.txt, Page_URL, redirect, and Image_URL requests exactly once toward the 52,428,800-byte total regardless of transfer-chunk boundaries or later processing, rejection, or disposal and additionally count each Page_URL HTML byte or compressed image byte toward its applicable per-response limit.
7. IF a declared body length exceeds an applicable byte limit or accepting the next streamed response-body byte would exceed an applicable per-response or total limit, THEN THE Web_Radar SHALL stop the affected request before retaining or analyzing a byte beyond the limit, record a resource-limit result, and preserve completed results.
8. IF image header inspection or decoding determines that an image exceeds 40,000,000 pixels, THEN THE Web_Radar SHALL stop decoding that image, record a resource-limit result, and preserve results for other Image_URL values.
9. WHEN a Scan reaches 100 unique Image_URL values or 52,428,800 total response-body bytes, THE Web_Radar SHALL stop scheduling additional image requests, classify every remaining discovered but unscheduled Image_URL as skipped, and display the skipped count.
10. WHEN elapsed monotonic time reaches 120 seconds, THE Web_Radar SHALL stop scheduling work, cancel outstanding requests and unfinished analysis, preserve completed results, and label the partial Scan incomplete due to timeout.
11. WHEN a user selects cancel for an active or robots-confirmation-paused Scan, THE Web_Radar SHALL stop scheduling work, cancel outstanding requests and unfinished analysis, issue no subsequent request for that Scan, preserve completed results, and label the partial Scan incomplete due to user cancellation.
12. IF a connection attempt reaches five elapsed monotonic seconds without establishing a connection or a connected request reaches 15 elapsed monotonic seconds without receiving the next response byte, THEN THE Web_Radar SHALL stop the affected request, report the connection-timeout or read-timeout category, and preserve completed results.
13. THE Web_Radar SHALL identify every outbound Scan request with the same nonempty Provenance user-agent string and project information URL.

### Requirement 9: Live Image Discovery and In-Memory Analysis

**User Story:** As a creator, I want Web Radar to inspect images and their surrounding context on a live page, so that potential uses of registered work can be reviewed.

#### Acceptance Criteria

1. WHEN Web_Radar receives an allowed HTML response containing at most 2,097,152 bytes, THE Web_Radar SHALL parse the response with Beautiful Soup and produce an image-candidate sequence by visiting `img` elements in document order and inspecting each element’s nonempty `src`, nonempty `srcset` candidates from left to right, and nonempty `data-src` in that order.
2. WHEN Web_Radar encounters an image candidate, THE Web_Radar SHALL resolve the candidate against the final Page_URL, retain the candidate only when the result is an absolute HTTP or HTTPS Image_URL, deduplicate by Normalized_URL, and enumerate at most 100 unique values in first-occurrence order.
3. WHEN Web_Radar analyzes an `img` element, THE Web_Radar SHALL collect exactly the Page_Context defined by the Glossary from the final parsed document and associate the Page_Context with the originating element, final Page_URL, and every resolved Image_URL originating from that element.
4. WHEN an allowed image response has an image media type and fully decodes with Pillow, THE Web_Radar SHALL retain response bytes and decoded pixels only in volatile process memory for that image’s analysis or while its Incident is the single current Incident_Triage selection and write none of those values to the Registry, files, temporary files, or persistent caches.
5. IF one unique Image_URL fails retrieval, redirect or SSRF validation, media-type validation, Scan_Budget enforcement, full decode, or extraction, THEN THE Web_Radar SHALL record exactly one failed result identifying that Image_URL and applicable failure category, preserve all other completed results unchanged, and continue with the next eligible Image_URL unless the Scan_Budget requires Scan termination.
6. WHEN Page_Context contains an Ecommerce_Indicator, THE Web_Radar SHALL display the matching text or markup as inert literal-text evidence without interpreting active content or classifying the use as infringement.
7. WHEN a Scan completes, a Scan is cancelled, or the current Incident_Triage selection changes, THE Web_Radar SHALL release all buffered response bytes and decoded pixels except those required to display at most the one Incident selected after that event.
8. WHEN Web_Radar displays a value derived from retrieved HTML, headers, DNS, WHOIS, image metadata, or image text, THE Web_Radar SHALL render the value as inert literal Streamlit text with HTML, Markdown, script, style, link, image, and active-content interpretation disabled.

### Requirement 10: Payload Cross-Validation and Incident Deduplication

**User Story:** As a creator, I want only registry-validated watermark findings recorded as incidents, so that random or malformed pixels do not create false registered matches.

#### Acceptance Criteria

1. WHEN Watermark_Engine returns a valid Watermark_Payload, THE Web_Radar SHALL query the Registry for the Registered_Asset identified by the extracted Asset_Hash.
2. WHEN a Registry record’s Asset_Hash and Creator_ID equal the corresponding extracted Watermark_Payload fields, THE Web_Radar SHALL classify the extraction as a Verified_Match.
3. IF no Registered_Asset matches both extracted fields, THEN THE Web_Radar SHALL label the extraction as unregistered and create no Incident for that extraction.
4. IF Watermark_Engine returns No_Watermark or Corrupt_Watermark, THEN THE Web_Radar SHALL create no Incident for that image.
5. WHEN Web_Radar discovers a Verified_Match whose `(Asset_Hash, exact Normalized_URL Page_URL, Normalized_URL Image_URL)` combination has no existing Incident and whose `(Asset_Hash, exact Normalized_URL Page_URL)` scope has no Whitelist_Entry, THE Registry SHALL create exactly one Incident with `Detected` status, equal first-seen and last-seen discovery timestamps, Page_Context, and extraction evidence.
6. WHEN Web_Radar rediscovers a Verified_Match with an existing Incident for the same `(Asset_Hash, exact Normalized_URL Page_URL, Normalized_URL Image_URL)` combination, THE Registry SHALL update that Incident’s last-seen timestamp and latest Page_Context without replacing the first-seen timestamp or creating another Incident.
7. WHEN Web_Radar discovers a Verified_Match whose `(Asset_Hash, exact Normalized_URL Page_URL)` scope has a Whitelist_Entry, THE Registry SHALL create or update the unique Incident for the matching `(Asset_Hash, exact Normalized_URL Page_URL, Normalized_URL Image_URL)` combination with `Fair Use` status, retain the discovery evidence, and suppress the Incident from the Active_Incident view.

### Requirement 11: Incident Evidence Review

**User Story:** As a creator, I want complete evidence displayed before taking action, so that I can make an informed decision about each detected use.

#### Acceptance Criteria

1. WHEN a user opens an Incident for which both the registered Source_Image representation and scraped target image representation are available, THE Incident_Triage SHALL display the two representations side by side.
2. IF a source or target image representation is unavailable, THEN THE Incident_Triage SHALL display a labeled placeholder and retain the associated Asset_Hash, URL, timestamp, and failure reason.
3. WHEN a user opens an Incident, THE Incident_Triage SHALL display Page_URL, Image_URL, Page_Context, Ecommerce_Indicators, Asset_Hash, Creator_ID, registration timestamp, first-seen timestamp, last-seen timestamp, and Incident_Status.
4. THE Incident_Triage SHALL label extracted context and Ecommerce_Indicators as evidence rather than a legal conclusion.
5. WHEN an Incident has `Detected` status, THE Incident_Triage SHALL offer `Strike Authorized`, `Mark Fair Use`, and `Request Credit` actions.
6. WHEN a user chooses an Incident action, THE Incident_Triage SHALL display the current Incident_Status, proposed Incident_Status, and every Whitelist_Entry or Audit_Event effect before requesting confirmation.
7. IF a user cancels or declines confirmation of an Incident action, THEN THE Incident_Triage SHALL leave the Incident_Status and all stored evidence unchanged and continue displaying the selected Incident’s evidence.
8. IF the Registry cannot commit every persistent change required by a confirmed Incident action, THEN THE Registry SHALL retain no Incident_Status, Whitelist_Entry, or Audit_Event change from that action and preserve all stored evidence for the selected Incident.
9. IF a confirmed Incident action fails, THEN THE Incident_Triage SHALL identify the failed action, continue displaying the selected Incident’s evidence, and make the action available for another user attempt.

### Requirement 12: Fair-Use Whitelist Semantics

**User Story:** As a creator, I want exact local fair-use exceptions, so that approved uses stop appearing as active incidents without suppressing unrelated pages.

#### Acceptance Criteria

1. WHEN a user confirms `Mark Fair Use` for an Incident with a rationale containing 1 through 500 Unicode code points and no NUL code point, THE Registry SHALL create or update one Whitelist_Entry scoped to the Incident’s Asset_Hash and exact Normalized_URL Page_URL.
2. WHEN a Whitelist_Entry is created, THE Registry SHALL set every Incident with the same Asset_Hash and exact Normalized_URL Page_URL to `Fair Use` in the same transaction.
3. WHILE a Whitelist_Entry exists for an Asset_Hash and exact Normalized_URL Page_URL, THE Incident_Triage SHALL exclude Incidents with that Asset_Hash and exact Normalized_URL Page_URL from the default Active_Incident view and include those Incidents in a labeled Fair Use view.
4. THE Registry SHALL determine Whitelist_Entry scope by exact equality of both Asset_Hash and Normalized_URL Page_URL while preserving path case and query bytes in the Page_URL comparison.
5. IF an Incident’s Asset_Hash differs from a Whitelist_Entry’s Asset_Hash or the exact Normalized_URL Page_URL differs by scheme, host, port, path case, path, or query bytes, THEN THE Registry SHALL avoid applying that Whitelist_Entry to the Incident.
6. WHEN a user removes a Whitelist_Entry scoped to an Asset_Hash and exact Normalized_URL Page_URL, THE Registry SHALL delete only that Whitelist_Entry and set each matching unresolved `Fair Use` Incident to `Detected` without authorizing a strike.
7. WHEN a user confirms `Mark Fair Use` for an Asset_Hash and exact Normalized_URL Page_URL that already has a Whitelist_Entry, THE Registry SHALL retain one Whitelist_Entry for that scope and update its rationale and modification timestamp.
8. IF a Fair Use rationale contains fewer than 1 or more than 500 Unicode code points or contains a NUL code point, THEN THE Incident_Triage SHALL identify the invalid rationale, keep confirmation unavailable, and leave the Registry unchanged.

### Requirement 13: Credit Request Workflow

**User Story:** As a creator, I want a non-adversarial outreach option, so that I can request attribution before considering a formal notice.

#### Acceptance Criteria

1. WHEN a user selects `Request Credit`, THE Incident_Triage SHALL generate an editable template containing 1 through 5,000 Unicode code points that includes the Incident’s Asset_Hash and Creator_ID as work identification, the Incident’s exact Page_URL, requested-attribution text containing 1 through 500 Unicode code points, and a creator-selected reply contact containing 1 through 320 Unicode code points.
2. WHEN a user submits a valid Credit Request template for confirmation, THE Incident_Triage SHALL display the complete current template, explain that confirmation will set Incident_Status to `Credit Requested`, bind confirmation to the displayed content, and require renewed confirmation after any edit.
3. WHEN the user confirms the displayed valid Credit Request template, THE Registry SHALL set Incident_Status to `Credit Requested` and create exactly one Audit_Event for that state change in the same transaction.
4. THE Incident_Triage SHALL label each Credit Request as an attribution request that is neither a legal notice nor a determination of ownership or infringement.
5. WHEN the Registry has committed the `Credit Requested` state and the user separately activates delivery for the exact confirmed template, THE Provenance_System SHALL open the template in the user’s chosen communication tool for user-controlled transmission without transmitting the Credit Request directly, automatically, or in the background.
6. IF a Credit Request template is empty, exceeds 5,000 Unicode code points, omits or changes the Incident’s Asset_Hash, Creator_ID, or exact Page_URL, contains requested-attribution text outside 1 through 500 Unicode code points, contains a reply contact outside 1 through 320 Unicode code points, or contains a NUL code point, THEN THE Incident_Triage SHALL identify every invalid or missing item, keep confirmation and delivery unavailable, and leave the Registry unchanged.
7. IF the user cancels before activating delivery or template generation, validation, confirmation, Registry update, or communication-tool opening fails, THEN THE Provenance_System SHALL identify the cancellation or failed operation, retain entered template content for further review, preserve the last committed Incident_Status and Audit_Event state, and perform no direct transmission.

### Requirement 14: Live Infrastructure and Abuse Contact Resolution

**User Story:** As a creator, I want current provider and abuse-contact evidence, so that a notice can be addressed without fabricated routing information.

#### Acceptance Criteria

1. WHEN a user authorizes a strike investigation, THE Strike_Engine SHALL perform one live DNS resolution for the Page_URL host and end the operation with returned records, no records, failure, or timeout within five elapsed monotonic seconds.
2. WHEN a user authorizes a strike investigation, THE Strike_Engine SHALL perform one live WHOIS lookup for the Page_URL host using a five-second connection timeout, a 15-second next-byte read timeout, a 20-second total elapsed monotonic timeout, and a 1,048,576-byte response limit.
3. WHEN live DNS or WHOIS data is returned within the applicable limits, THE Strike_Engine SHALL display only values present in that returned data, limited to 100 distinct public addresses, 100 distinct canonical names of at most 253 ASCII characters each, 10 registrar values and 10 organization values of at most 500 Unicode code points each, and 100 distinct WHOIS email addresses of at most 254 ASCII characters each, together with source type and a UTC lookup timestamp in `YYYY-MM-DDTHH:MM:SSZ` format.
4. WHEN WHOIS data contains syntactically valid email addresses of 3 through 254 ASCII characters with exactly one `@` separator and nonempty local and domain parts, THE Strike_Engine SHALL rank addresses containing the case-insensitive ASCII substring `abuse` ahead of other WHOIS contact addresses and require the user to select the recipient.
5. IF DNS or WHOIS returns no bounded valid data, fails, times out, returns malformed data, or exceeds an applicable byte, count, or length limit, THEN THE Strike_Engine SHALL identify the affected lookup and observed condition, indicate whether the other lookup succeeded, display only complete values returned within limits, identify omitted excess output, and invent or infer no provider, organization, address, or contact value.
6. IF WHOIS returns no suitable abuse contact, THEN THE Strike_Engine SHALL require a user-entered recipient address containing 3 through 254 ASCII characters with exactly one `@` separator and nonempty local and domain parts, label the address as user-entered, and prevent dispatch preparation until the address is valid.
7. THE Strike_Engine SHALL label registrar and organization fields as provider candidates rather than verified hosting-provider identities.
8. WHEN the Strike_Engine performs a DNS-dependent network action, THE Strike_Engine SHALL apply the Page_URL URL, DNS, and Public_Network_Address checks before opening a connection and apply connected-peer verification immediately after connection establishment and before transmitting a request byte or accepting a response byte.

### Requirement 15: DMCA Notice Completeness and Legal Guardrails

**User Story:** As a creator, I want a complete but reviewable notice template, so that I can decide whether to send an accurate takedown request.

#### Acceptance Criteria

1. WHEN the user supplies a copyrighted-work identification containing 1 through 2,000 Unicode code points, a signatory name containing 1 through 200 Unicode code points, a postal address containing 1 through 500 Unicode code points, a telephone number containing 1 through 32 Unicode code points including at least one ASCII digit, a contact email containing 3 through 254 Unicode code points with exactly one `@` separator, nonempty local and domain parts, and no Unicode whitespace, and an electronic signature containing 1 through 200 Unicode code points, with no required field empty, composed only of Unicode whitespace, or containing a NUL code point, THE Strike_Engine SHALL compile a DMCA_Notice that identifies the copyrighted work, identifies the material by Page_URL and Image_URL, reproduces the contact information, states the good-faith belief, states the accuracy and authority declaration under penalty of perjury, and reproduces the electronic signature.
2. WHEN the Strike_Engine compiles a DMCA_Notice for an Incident, THE Strike_Engine SHALL include Asset_Hash, Creator_ID, registration timestamp, incident first-seen timestamp, Page_URL, Image_URL, and watermark verification result from the Incident’s Verified_Match and linked Registered_Asset without permitting user alteration of those evidence values.
3. WHILE the Strike_Engine displays a DMCA_Notice preview or Dispatch_Card, THE DMCA_Notice SHALL state that the watermark, Asset_Hash, and local timestamps support identification but do not independently prove ownership, infringement, fair use, provider liability, or legal entitlement.
4. WHILE the Strike_Engine displays a DMCA_Notice preview or Dispatch_Card, THE Provenance_System SHALL state that Provenance provides evidence-collection and workflow assistance rather than legal advice or a determination of ownership, infringement, fair use, provider liability, or legal validity.
5. WHEN a user requests dispatch readiness for a DMCA_Notice, THE Strike_Engine SHALL present seven separate confirmations covering ownership or authorization, good-faith belief, accuracy of every factual assertion, acknowledgement of the penalty-of-perjury statement, consideration of authorization by law including fair use, adoption of the displayed electronic signature, and responsibility for the notice’s factual and legal assertions.
6. WHEN the user selects a notice confirmation or delivery-readiness confirmation, THE Strike_Engine SHALL bind that confirmation only to its named attestation and the exact Incident, recipient, subject, and full notice body then displayed.
7. IF a required user-entered text field is empty, exceeds its criterion 1 maximum, contains only Unicode whitespace, or contains a NUL code point; the telephone number contains no ASCII digit; a contact or recipient email lacks exactly one `@` separator with nonempty local and domain parts or contains Unicode whitespace; or an evidence value differs from the related Incident, Verified_Match, or Registered_Asset, THEN THE Strike_Engine SHALL identify every invalid field, keep dispatch disabled, open no email draft, and leave the Registry unchanged.
8. IF the related Incident lacks `Strike Authorized` status, an exact Whitelist_Entry exists for the Incident’s Asset_Hash and Page_URL, the Incident lacks a Verified_Match or linked Registered_Asset, the recipient is invalid, the subject contains fewer than 1 or more than 200 Unicode code points, the full notice body contains fewer than 1 or more than 20,000 Unicode code points, a required notice or evidence field is missing or invalid, any of the seven confirmations is absent or stale, or delivery-readiness confirmation is absent or stale, THEN THE Strike_Engine SHALL keep dispatch disabled, identify every missing or invalid prerequisite, open no email draft, and leave the Registry unchanged.
9. WHEN a user edit or refreshed evidence changes the selected Incident, recipient, subject, copyrighted-work identification, material identification or location, signatory name, contact information, evidence value, notice-body statement, or electronic signature after confirmation, THE Strike_Engine SHALL regenerate the notice preview, clear all seven notice confirmations and delivery-readiness confirmation, keep dispatch disabled, and require each cleared confirmation again for the changed displayed values.

### Requirement 16: User-Controlled Dispatch

**User Story:** As a creator, I want final control over external communications, so that the application cannot send a legal notice without my informed action.

#### Acceptance Criteria

1. WHEN every DMCA_Notice field, evidence value, prerequisite, and notice confirmation is valid and complete, THE Strike_Engine SHALL display a Dispatch_Card containing the exact recipient, subject, and full body that will populate the local email draft.
2. WHEN the user requests dispatch from a displayed Dispatch_Card, THE Dispatch_Card SHALL require explicit confirmation that the displayed recipient, subject, and full body are ready for delivery before enabling a draft-opening attempt.
3. WHEN the user provides delivery-readiness confirmation and activates dispatch, THE Dispatch_Card SHALL attempt to open a draft containing the displayed recipient, subject, and full body in the user’s local email client without transmitting the email directly from the Provenance_System.
4. THE Provenance_System SHALL avoid background, scheduled, bulk, and automatic notice dispatch.
5. WHEN a draft-opening attempt completes without a reported failure or cancellation, THE Incident_Triage SHALL require the user to select `Sent`, `Not Sent`, or `Cancel` before recording a dispatch outcome.
6. WHEN the user explicitly selects `Sent` for the current draft-opening attempt, THE Registry SHALL record recipient, notice-content SHA-256 hash, user-confirmation timestamp, and related Incident identifier in exactly one Audit_Event without storing the full notice body.
7. IF the user selects `Not Sent` or `Cancel`, provides no outcome, or the draft-opening attempt fails or is cancelled, THEN THE Registry SHALL avoid recording a successful dispatch for that attempt.
8. WHILE the user has not explicitly selected `Sent` for the current draft-opening attempt, THE Provenance_System SHALL avoid presenting the notice as successfully sent.
9. IF no local email client is available or the local environment reports that draft opening failed or was cancelled before opening, THEN THE Dispatch_Card SHALL state that the draft was not opened, retain the unchanged recipient, subject, and full body for another user-initiated attempt, and leave the Registry unchanged.

### Requirement 17: Security and Privacy Controls

**User Story:** As a creator, I want local data and untrusted web content handled safely, so that evidence gathering does not expose private information or execute hostile content.

#### Acceptance Criteria

1. THE Provenance_System SHALL disable telemetry, analytics, cloud storage, and remote application logging by default.
2. THE Provenance_System SHALL avoid transmitting Source_Image bytes, Watermarked_Image bytes, Creator_Metadata, Registry records, or notice contents except to a user-selected target through an explicit user action.
3. THE Provenance_System SHALL avoid executing scripts, styles, active content, downloaded binaries, or embedded HTML retrieved during a Scan.
4. THE Provenance_System SHALL encode untrusted and user-provided strings for safe Streamlit text display.
5. THE Provenance_System SHALL avoid writing scraped image bytes to persistent storage.
6. THE Provenance_System SHALL avoid storing image bytes, full notice bodies, postal addresses, or contact emails in diagnostic logs.
7. WHEN a user requests deletion of a Registered_Asset, THE Provenance_System SHALL make no Registry change and display a deletion preview containing only the Asset_Hash, exact current counts of dependent Incidents and Whitelist_Entries, exact current count of related Audit_Events that will be retained, a statement distinguishing deleted records from retained Audit_Events, and separate confirmation and cancellation controls.
8. WHEN a user confirms the currently displayed deletion preview, THE Registry SHALL delete the identified Registered_Asset, every dependent Incident, and every dependent Whitelist_Entry in one transaction while retaining every related Audit_Event with the Asset_Hash as a tombstone reference.
9. IF a user cancels or dismisses a deletion preview without confirming deletion, THEN THE Provenance_System SHALL leave the Registered_Asset, dependent Incidents, dependent Whitelist_Entries, and related Audit_Events unchanged.
10. IF the identified Registered_Asset no longer exists or a current dependent-record or related-Audit_Event count differs from the displayed deletion preview, THEN THE Provenance_System SHALL perform no deletion, display a refreshed preview, and require new explicit confirmation.
11. IF deletion of the Registered_Asset or a dependent record, a constraint check, or transaction commit fails, THEN THE Provenance_System SHALL report that deletion did not complete, restore the complete pre-transaction Registry state, and exclude Creator_Metadata, notice contents, contact emails, postal addresses, and local paths from the failure indication.
12. WHEN the Provenance_System displays a local file or Registry path, THE Provenance_System SHALL avoid converting the path into a remotely accessible URL.

### Requirement 18: Reliability, Failure Isolation, and Auditability

**User Story:** As an operator, I want failures isolated and material actions auditable, so that partial network problems do not corrupt evidence or hide outcomes.

#### Acceptance Criteria

1. IF one Image_URL retrieval, decode, or extraction fails and no Scan_Budget limit requires Scan termination, THEN THE Web_Radar SHALL assign exactly one failure outcome and category to that attempted Image_URL, preserve every completed image outcome and committed Incident from the Scan, and continue with the next eligible Image_URL without exceeding the Scan_Budget.
2. IF Page_URL retrieval fails because of an HTTP, timeout, DNS, TLS, SSRF, robots, or resource-limit condition, THEN THE Web_Radar SHALL terminate the Scan, create no Incident or other Registry change for that Scan, preserve the pre-Scan Registry state, and display the applicable failure category.
3. WHEN a Scan reaches a completed, failed, cancelled, or Scan_Budget-terminated outcome, THE Web_Radar SHALL display counts for discovered candidates, attempted images, Verified_Matches, No_Watermark results, Corrupt_Watermark results, valid-but-unregistered payloads, failures, cancelled attempts, and skipped images together with total streamed response-body bytes and elapsed monotonic time.
4. WHEN Web_Radar calculates a Scan summary, THE Web_Radar SHALL count each unique retained Normalized_URL as one discovered candidate, each candidate for which validation, retrieval, or analysis began as one attempted image, each attempted image in exactly one Verified_Match, No_Watermark, Corrupt_Watermark, valid-but-unregistered, failure, or cancelled category, and each discovered candidate for which no attempt began as one skipped image.
5. WHEN Web_Radar calculates Scan resource totals, THE Web_Radar SHALL include every response-body byte and elapsed monotonic interval governed by the Scan_Budget without double-counting a response-body byte.
6. WHEN a user confirms an Incident_Status change, Whitelist_Entry creation or removal, strike-investigation authorization, or dispatch outcome, THE Registry SHALL commit every Registry change caused by that confirmation and exactly one corresponding Audit_Event in one transaction.
7. WHEN the Registry creates an Audit_Event, THE Audit_Event SHALL contain the confirmed action’s event type, every related record identifier, previous and new Incident_Status for each changed Incident, a whole-second UTC timestamp, and a content hash when applicable.
8. WHEN a retry repeats a previously committed Registry operation with an identical operation type, target identifiers, requested values, and content hash, THE Registry SHALL return the previously committed outcome without creating, updating, or deleting another Registry record, changing a timestamp, or creating another Audit_Event.
9. IF a user cancels a Scan or the Scan_Budget requires termination after image discovery begins, THEN THE Web_Radar SHALL stop scheduling attempts, cancel unfinished attempts, preserve completed outcomes and Incidents committed before termination, classify each started unfinished attempt as cancelled, classify each discovered unstarted Image_URL as skipped, create no Incident for a cancelled or skipped image, and display the Scan as incomplete with the termination reason.
10. IF a Registry change or Audit_Event required for a confirmed action cannot commit, THEN THE Registry SHALL roll back the complete transaction, preserve the exact pre-action Registry state, and return a failure indication that the action was not recorded.
11. WHEN the Registry is first accessed after local process termination before an in-progress transaction committed, THE Registry SHALL expose the exact pre-transaction state and no Registry record or Audit_Event from the uncommitted transaction.
12. WHEN the Registry is first accessed after local process termination after an in-progress transaction committed but before its outcome was returned, THE Registry SHALL expose the complete committed state including the single required Audit_Event and apply criterion 8 to an identical retry.

### Requirement 19: Accessibility and Inclusive Interaction

**User Story:** As a creator using assistive technology, I want the dashboard to communicate structure, evidence, and actions without relying on vision or a pointer, so that I can complete the workflow independently.

#### Acceptance Criteria

1. WHILE an input, action, status, image, or validation message is displayed, THE Dashboard SHALL display a persistent text label identifying the item and expose the same text to assistive technology as the item’s accessible name or description.
2. THE Dashboard SHALL permit a keyboard-only user to reach, operate, and dismiss every interactive control in every workflow without pointer input or a keyboard trap.
3. WHEN form submission fails validation, the selected tab changes, a dialog opens, or an action completes, THE Dashboard SHALL move keyboard focus respectively to the error summary, active tab-panel heading, dialog heading, or completion-status message.
4. THE Dashboard SHALL communicate every Incident_Status, match state, failure, and Ecommerce_Indicator using visible text exposed to assistive technology, with color and icons used only as supplemental indicators.
5. THE Dashboard SHALL provide alternative text for each image that identifies the image as a registered original, scraped target, placeholder, or watermark result as applicable.
6. THE Dashboard SHALL render normal text with a contrast ratio of at least 4.5:1, text at least 18 points or at least 14 points and bold with a contrast ratio of at least 3:1, and visual information required to identify user-interface components and states with a contrast ratio of at least 3:1 against adjacent colors.
7. WHEN an operation that has remained incomplete for at least one second reports changed progress or state, completes, or fails, THE Dashboard SHALL expose within one second a textual update identifying the operation and new progress, completion, or failure state to assistive technology without moving keyboard focus.
8. IF a submission contains one or more validation errors, THEN THE Dashboard SHALL display a textual error summary with one entry for each invalid field, identify each field and violated constraint, associate each entry with the corresponding labeled field, preserve all user-entered values, and prevent completion of the requested action.
9. WHEN a dialog closes, THE Dashboard SHALL return keyboard focus to the control that opened the dialog.

### Requirement 20: Executable Correctness Properties

**User Story:** As a maintainer, I want invariant and round-trip properties stated precisely, so that automated property-based tests can exercise edge cases across the local logic.

#### Acceptance Criteria

1. WHEN The_Forge hashes two decodable Source_Image values having equal decoded widths, equal decoded heights, and equal eight-bit RGB channel values at every pixel position, THE The_Forge SHALL produce equal Asset_Hash values.
2. IF two generated Canonical_Source byte sequences differ in at least one byte and the two computed Asset_Hash values are equal, THEN THE The_Forge SHALL report the generated pair as an Asset_Hash collision and retain the generated case.
3. WHEN the Payload_Parser receives bytes emitted by the Payload_Serializer for a Watermark_Payload containing a valid Asset_Hash, Creator_ID, and UTC timestamp, THE Payload_Parser SHALL return exactly the three values supplied to the Payload_Serializer.
4. WHEN the Payload_Serializer receives fields returned by the Payload_Parser for a canonical serialized Watermark_Payload, THE Payload_Serializer SHALL reproduce the original serialized Watermark_Payload byte for byte.
5. WHEN the Watermark_Engine receives a generated Source_Image and valid Watermark_Payload whose serialized byte length is at most Watermark_Capacity, THE Watermark_Engine SHALL produce a Watermarked_Image from which extraction returns exactly the embedded Asset_Hash, Creator_ID, and UTC timestamp.
6. WHEN the Watermark_Engine successfully embeds a Watermark_Payload, THE Watermark_Engine SHALL preserve Source_Image width, height, every alpha value, and the seven most-significant bits of every RGB channel in the Watermarked_Image.
7. WHEN serialized Watermark_Payload byte length equals Watermark_Capacity, THE Watermark_Engine SHALL complete embedding and produce a Watermarked_Image.
8. IF serialized Watermark_Payload byte length exceeds Watermark_Capacity by one or more bytes, THEN THE Watermark_Engine SHALL reject embedding and produce no Watermarked_Image.
9. WHEN exactly one embedded bit within the Watermark_Payload body is inverted without changing the stored CRC-32, THE Watermark_Engine SHALL return Corrupt_Watermark without returning identity fields or reporting a Verified_Match.
10. IF payload bytes are invalid UTF-8, malformed JSON, noncanonical, duplicate-keyed, missing a required field, contain an extra field, contain a non-string field, invalid Asset_Hash, invalid Creator_ID, or invalid timestamp, THEN THE Payload_Parser SHALL return Corrupt_Watermark without returning identity or timestamp fields.
11. WHEN the Registry receives a second or subsequent valid registration request with the same Asset_Hash and Creator_ID as the first successful request, THE Registry SHALL return the existing Registered_Asset, retain exactly one record for that Asset_Hash, and preserve original registration timestamp and Creator_Metadata.
12. WHEN the Registry processes each permutation of the same generated collection containing 0 through 100 Verified_Matches from identical pre-operation Registry states, THE Registry SHALL produce the same set of unique `(Asset_Hash, Normalized_URL Page_URL, Normalized_URL Image_URL)` Incident keys for every permutation.
13. WHEN the Provenance_System normalizes an accepted Page_URL or Image_URL and then normalizes the resulting Normalized_URL, THE Provenance_System SHALL produce a second Normalized_URL that is byte-for-byte equal to the first.
14. WHILE a Whitelist_Entry exists for an Asset_Hash and exact Normalized_URL Page_URL, THE Registry SHALL suppress every Incident having that Asset_Hash and exact Normalized_URL Page_URL from the Active_Incident view.
15. IF an Incident has `Detected`, `Strike Authorized`, or `Credit Requested` status and every Whitelist_Entry differs in Asset_Hash or exact Normalized_URL Page_URL, THEN THE Registry SHALL retain the Incident in the Active_Incident view, including when a URL differs by scheme, host, port, path case, path, or query bytes.
16. IF a generated insertion, update, deletion, constraint-check, or commit failure occurs before a Registry transaction commits, THEN THE Registry SHALL expose the same records, field values, and record relationships as the pre-operation Registry state.
17. WHILE Web_Radar processes a generated response sequence for one Scan, THE Web_Radar SHALL accept at most 2,097,152 HTML response bytes, enumerate at most 100 unique Image_URL values, accept at most 10,485,760 compressed response bytes per image, decode at most 40,000,000 pixels per image, accept at most 52,428,800 total response-body bytes, follow at most five redirects per request, allow at most five elapsed monotonic seconds per connection attempt and 15 elapsed monotonic seconds between response bytes, and process the Scan for at most 120 elapsed monotonic seconds.
18. WHEN the user supplies every required valid identity and contact input and the Incident supplies every required evidence input, THE Strike_Engine SHALL produce a DMCA_Notice containing every statutory section and every Asset_Hash, Creator_ID, registration timestamp, Incident first-seen timestamp, Page_URL, Image_URL, and watermark verification field.
19. IF a generated dispatch-input combination omits or invalidates a required notice field, evidence field, recipient, subject, notice body, any of the seven notice confirmations, or delivery-readiness confirmation, THEN THE Strike_Engine SHALL keep dispatch disabled and identify every omitted, invalid, or stale prerequisite.
20. IF extraction returns No_Watermark, Corrupt_Watermark, or a valid Watermark_Payload whose Asset_Hash and Creator_ID do not both match one Registered_Asset, THEN THE Provenance_System SHALL create no Incident and leave the Registry Incident key set unchanged.

### Requirement 21: Scope and User Accountability

**User Story:** As a responsible user, I want the application’s capabilities and limitations stated at decision points, so that I remain accountable for scans and communications.

#### Acceptance Criteria

1. WHILE the user has not acknowledged authorization to access submitted public Page_URL values and responsibility for applicable site terms and law during the current application session, WHEN the user requests a Scan, THE Web_Radar SHALL require the acknowledgement and prevent the Scan from beginning.
2. WHILE the user has not acknowledged during the current application session that Infrastructure_Evidence can be incomplete or inaccurate, WHEN the user requests a strike investigation, THE Strike_Engine SHALL require the acknowledgement and prevent the investigation from beginning.
3. WHEN a user requests dispatch of a DMCA_Notice, THE Provenance_System SHALL require acknowledgement of responsibility for the factual and legal assertions in that exact DMCA_Notice before dispatch can proceed.
4. WHEN the Provenance_System displays a Verified_Match, Ecommerce_Indicator, registration timestamp, or Infrastructure_Evidence, THE Provenance_System SHALL label the displayed item as evidence and state that the item individually or together with other displayed items does not determine infringement or fair use.
5. WHEN the Provenance_System displays a Scan summary, THE Provenance_System SHALL state that the Scan parses retrieved static HTML without browser JavaScript execution and can omit content or images made available through browser JavaScript execution.
6. THE Provenance_System SHALL begin each scan, triage, whitelist, credit-request, strike-investigation, and dispatch action only in response to the user’s activation of a control for that specific action.
