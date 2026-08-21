# Provenance

A local-first workspace for image creators who want to prove an image is theirs and find out
where it ended up. Provenance embeds a verifiable watermark in your own artwork, records it in
a SQLite registry on your machine, and checks a public page you name for images that carry your
watermark.

Everything runs on your computer. There is no account, no server, no telemetry, and no paid
API. Your images and your contact details never leave the machine.

Built for the Ready, Spec, Ship hackathon with [Kiro](https://kiro.dev). The complete spec that
drove the build lives in [`.kiro/specs/provenance/`](.kiro/specs/provenance/).

## The problem

An artist posts work online. Weeks later it appears on a print-on-demand storefront with the
signature cropped off. The artist has no way to show the file on that page came from theirs,
and the tools that claim to help are cloud services that want the original artwork uploaded to
somebody else's server as the price of protection.

Two things are missing. First, evidence: something inside the pixels that survives a crop of
the visible signature and ties the file to a specific creator at a specific moment. Second,
restraint: the tool should collect facts and hand them to a human, not fire off legal threats
on a match.

## The solution

Three tabs, one direction of travel.

**The Forge** takes your image and your creator details, embeds a watermark in the pixel
least-significant bits, and writes a record to your local registry. The identity is a SHA-256
hash over a canonical form of the decoded pixels, so re-encoding a PNG as a different PNG, or
stripping metadata, or adding an alpha channel, all produce the same identity. You get a
lossless PNG back, verified pixel-for-pixel against the input before the download is offered.

**Web Radar** fetches one page you name, reads its static HTML, finds the images, downloads
each one, and looks for a watermark. A watermark alone is not a match. The extracted payload
has to name an asset in your registry *and* name you as the creator of record before anything
is recorded as an incident. Alongside a match, Provenance keeps the page context it found next
to that image: the title, the nearest heading, the caption, the alt text, and any commerce
wording such as a price or an add-to-cart control.

**Incident Triage** is where a confirmed match becomes a decision. It lists active incidents
and fair-use ones separately, shows everything recorded about the one you select, and lets you
mark a use as fair, or take that back later. Nothing is written until you review a preview of
exactly what would change and confirm it. The preview is fingerprinted, so if the incident or
your rationale changes after you reviewed it, the confirmation is refused and you are asked to
look again.

### Key features

- Canonical pixel identity that ignores container, metadata, alpha channel, and array layout.
- LSB watermark with a 13-byte header and a CRC-32 that detects any single bit flip. Corruption
  is reported as corruption, never as a match.
- Lossless PNG output, verified against the source before download.
- Two-fact verification. Asset hash and creator ID must both agree with the registry.
- Fair-use scope is exact. An entry covers one asset and one byte-exact page address, so a
  neighbouring page, or the same path in different case, is never suppressed.
- Every decision is previewed, fingerprinted, confirmed, then committed with one audit event
  and an idempotency receipt, so a double click cannot record the same thing twice.
- Every outbound request resolves DNS itself, connects to one pinned address, and compares
  `getpeername()` against the pinned answer before writing a single request byte. Only public
  unicast addresses are accepted, and every redirect hop repeats the whole check.
- robots.txt is fetched and honoured before the page is touched, per RFC 9309. If robots.txt
  cannot be read, the scan pauses and asks you.
- A hard budget on every scan: bytes, images, pixels, redirects, and time.
- Scraped image bytes are never written to disk. Pixels for at most one incident are held in
  memory at a time, behind a lease.
- Untrusted text renders through a text-only helper. `unsafe_allow_html=True` appears nowhere in
  the application, so a scraped caption cannot become markup, a link, or a remote image request.
- The only markup in the app is one static stylesheet, passed to Streamlit as a file path so no
  runtime value can reach the document. Tests assert that the stylesheet references no remote
  resource and that no other module inserts HTML.
- SQLite in STRICT mode with checksum-verified migrations, a startup integrity gate, and one
  audit event per material action.

## Prerequisites

- Python 3.13 (the project pins `>=3.13,<3.14`)
- Windows, macOS, or Linux
- An internet connection, only for the Web Radar tab

## Setup

Clone, create a virtual environment, install.

Windows (`cmd.exe`):

```
git clone https://github.com/joshrandomcodes/Provenance.git
```

```
cd Provenance
```

```
py -3.13 -m venv .venv
```

```
.venv\Scripts\python.exe -m pip install --upgrade pip
```

```
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

macOS and Linux:

```
git clone https://github.com/joshrandomcodes/Provenance.git
cd Provenance
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

`requirements.txt` is a full pinned freeze of the environment the project was built and tested
in. If you would rather install only the direct dependencies, use `pip install -e .` for the
runtime set and `pip install -e ".[dev]"` to add the test and lint tooling.

## Running it

```
.venv\Scripts\python.exe scripts\run_app.py
```

macOS and Linux:

```
.venv/bin/python scripts/run_app.py
```

Then open the address Streamlit prints, normally <http://127.0.0.1:8501>. The launcher binds to
loopback only, turns off Streamlit usage statistics, and disables the file watcher and magic
runner. No login and no credentials are needed; there is no account system.

On first run Provenance creates its registry outside the source tree:

- Windows: `%LOCALAPPDATA%\Provenance\registry.sqlite3`
- Linux: `$XDG_DATA_HOME/provenance/registry.sqlite3`, or `~/.local/share/provenance/`
- macOS: `~/.local/share/provenance/registry.sqlite3`

Set `PROVENANCE_HOME` to put it somewhere else.

`PROVENANCE_ENABLE_LOCAL_DIAGNOSTIC_LOG` is recognised but does nothing yet. The redacting
diagnostics sink is specified in the design and declared as a port, and no adapter implements
it, so setting the variable creates an empty `diagnostics` directory and writes no records. The
sidebar says so rather than reporting a log that does not exist.

### Look and feel

The dashboard is themed through `.streamlit/config.toml` plus one stylesheet at
`provenance/ui/theme.css`: a deep drifting gradient, thin widely spaced type, dimmed navigation
with a lit selection, and translucent panels on hairline rules. The visual style is inspired by
seventh-generation console dashboards. Every rule is original CSS using system font stacks, and
the stylesheet references no font, image, or remote resource of any kind, which a test enforces.

Each tab opens with a few short lines that reveal in sequence, stepped rather than faded, with a
caret on the last one. The sidebar carries the idle motion: a breathing edge, a heading that
brightens and dims, and a highlight travelling down the border. Where the browser supports
scroll-driven animations, panels rise as they enter view.

All of it is decoration and none of it is load-bearing. Nothing flashes: the fastest cycle is the
caret at about one hertz, well below the three-per-second accessibility threshold, and every
animation stops when the operating system asks for reduced motion. Values still render as plain
text, so if a future Streamlit release changes its DOM the styling degrades to the configured
palette without losing a single fact.

## Usage

### Watermark and register an image

1. Open **The Forge**.
2. Choose a PNG or JPEG. The upload cap is 26,214,400 bytes and 40,000,000 decoded pixels.
3. Fill in a Creator ID (letters, digits, `.`, `_`, `-`) and a display name. Contact email,
   postal address, and rights statement are optional.
4. Press **Watermark and register**. Registration commits before the download appears.
5. Download the watermarked PNG. Use that file wherever you publish, because the watermark is
   in the pixels of that file, not in the original.

Registration is idempotent per creator. Forging the same pixels again reuses the existing
record rather than creating a second one.

### Scan a page

1. Publish a watermarked image somewhere you control, or use a page you are authorised to
   request.
2. Open **Web Radar**, tick the authorisation statement, and paste the page address. Only
   `https://` and `http://` on ports 443 and 80 are accepted.
3. Press **Scan this page**. Progress updates live and **Cancel scan** works while it runs.
4. Read the report. Each image gets exactly one outcome, and a verified match expands by
   default with its page context and any commerce wording quoted verbatim.

A test page the project author controls is published at
<https://joshrandomcodes.github.io/Provenance/>, served from [`docs/`](docs/) in this
repository. It shows the author's own watermarked artwork presented as a print for sale, which
is the exact shape of the problem Provenance is built for.

Scanning that page from your own machine finds the image and reads its watermark, then reports
it as **Unregistered**, because your registry has no record of that asset. A verified match
requires the asset in your own registry, which is what step 4 of
[For judges](#for-judges) describes.

Scanning `localhost` or a private address is refused on purpose. The resolver accepts only
public unicast addresses, which is what stops the scanner being pointed at your own network.

### Record a decision

1. Open **Incident Triage**. Verified matches appear under **Active incidents**.
2. Pick an incident to see everything recorded about it: both addresses, the asset hash, the
   creator ID in the watermark and the one on record, the watermark checksum, the registration
   and discovery timestamps, the page context, and any commerce wording, quoted verbatim.
3. To accept a use, write a rationale and press **Review marking this fair use**. You get a
   preview naming the current status, the proposed status, every incident whose status would
   move, the fair-use entry that would be written, and the audit record. Nothing is stored yet.
4. Press **Confirm** to commit, or **Cancel** to walk away with nothing changed. Editing the
   rationale after a review invalidates it, and you will be asked to review again.
5. Marked incidents move to the **Fair use** list. **Review removing fair use** takes it back
   and returns the affected incidents to Detected. It authorizes nothing.

Neither image is displayed side by side, and the tab says why: the registry stores your image's
identity rather than its pixels, and scraped image bytes are never written to disk at all.

## Costs and rate limits

**There are no API costs.** Provenance calls no third-party or paid service. It talks only to
the page you name, over your own connection, and there is nothing to bill.

Rate limiting is self-imposed rather than external. Each scan is capped at:

| Limit | Value |
| --- | --- |
| HTML per page | 2 MiB |
| Bytes per image | 10 MiB |
| Bytes per scan | 50 MiB |
| Unique images per scan | 100 |
| Decoded pixels per image | 40,000,000 |
| Redirects per request | 5 |
| Connect timeout | 5 seconds |
| Time between bytes | 15 seconds |
| Total scan time | 120 seconds |

The clock does not stop while you answer the robots.txt prompt, so a slow decision means less
scanning time. Outbound requests identify themselves as
`Provenance/0.1.0 (+https://github.com/joshrandomcodes/Provenance)` so site operators can see
who is asking. One page per scan, no crawling, and no scan starts without an explicit click.

## Testing

The whole deterministic gate, lint plus format plus strict types plus tests:

```
.venv\Scripts\python.exe scripts\run_checks.py
```

macOS and Linux:

```
.venv/bin/python scripts/run_checks.py
```

As of 21 August 2026 that reports ruff lint and format clean, strict mypy clean under
`--strict`, and the whole deterministic suite passing in roughly two minutes.

Tests only:

```
.venv\Scripts\python.exe -m pytest
```

Markers are `unit`, `integration`, `contract`, `browser`, and `live`. The default run excludes
`browser` and `live`, so nothing in the standard suite reaches the internet. Contract tests
exercise the real transport against a scripted local HTTP server, reaching it only by injecting
a permissive address policy that production never uses.

71 of the tests are Hypothesis property suites covering canonical identity, the payload codec,
watermark round trips and capacity boundaries, transaction rollback, incident confluence, URL
normalisation, scan limits, byte accounting, deletion compare-and-swap, and material action
idempotency. `.hypothesis/` holds the local example database and is gitignored.

## Current state

Honest accounting, because a demo should not imply more than exists.

**Working end to end:** The Forge, Web Radar, Incident Triage with the fair-use decision, and
the whole domain, storage, and network stack beneath them. The core loop is verified against a
real public page, not only against tests.

**Not built yet:** Incident Triage offers Mark Fair Use and Remove Fair Use, and nothing else.
Strike authorization and credit requests are named in the tab as unimplemented rather than
shown as controls, because a button that flipped a status without the workflow behind it would
misrepresent what the tool does. Also unbuilt: DNS and WHOIS lookups for a scanned host, DMCA
notice templates, the local `mailto:` draft dispatch, and the asset deletion flow. Task status
is tracked in [`.kiro/specs/provenance/tasks.md`](.kiro/specs/provenance/tasks.md), which lists
what is complete, what is deliberately open, and why.

`python-whois` and `playwright` are pinned for that planned work and are not imported by the
application today.

### Limits worth knowing

- Images only, PNG and JPEG. No text, audio, or video.
- The watermark lives in least-significant bits. It survives lossless handling, direct
  hotlinking, and metadata stripping. It does **not** survive JPEG recompression, resizing, or
  cropping, so a platform that recompresses uploads will destroy it. Treat Provenance as
  evidence for hotlinked and losslessly copied files.
- Web Radar reads one page's static HTML and executes no JavaScript, so a gallery rendered
  client-side yields nothing.
- Provenance assists evidence collection. It does not provide legal advice and does not
  determine ownership, infringement, or fair use. There is no automated enforcement anywhere in
  the design: the planned dispatch path opens a draft in your own email client behind explicit
  attestations, and there is no SMTP and no background sending.

## How Kiro was used

The project was built spec-first in Kiro. Nothing here predates the hackathon.

**The spec is the artifact.** `.kiro/specs/provenance/` holds `requirements.md`, `design.md`,
and `tasks.md`, written and iterated with Kiro before implementation started. Requirements are
numbered and every module cites the requirement IDs it satisfies in its docstring, so any file
can be traced back to the clause that justifies it. `tasks.md` is a hierarchy of implementation
steps, each naming its requirements and its tests, and it stayed the source of truth throughout
the build.

**Direction stayed with me.** The security posture came from decisions I made and then had Kiro
implement: resolve DNS in-process rather than trusting a client library, pin the address,
verify the peer before writing a request byte, revalidate at every redirect hop, and never let
a scraped byte reach disk. The same applies to the product restraint. Early drafts of this
project described automated legal strikes; I cut that, and the design now requires a human
decision at every consequential step. That call shaped the architecture, and it is why
`cross_validation.py` is the only module allowed to promote an extraction to a match.

**Kiro wrote most of the code; I set the invariants.** Concretely, I directed the two-fact
verification rule, the single-budget-across-the-robots-pause rule, the exact-header watermark
format, the repositories-never-commit boundary, and the text-only rendering rule. Kiro turned
each into typed, tested modules and kept the layering clean, with domain code that imports no
framework and a composition root proven by test to be unable to import test fixtures.

**The most useful thing Kiro did was find a hole in my tests.** The suite was green, but the
pinned transport had a production-only defect. `http.client.HTTPConnection.getresponse()`
closes the caller's socket when the peer answers `Connection: close`, which every Provenance
request invites deliberately to avoid connection pooling. The socket died before the body
reader could set its next-byte deadline. The tests could not see it because Python's
`BaseHTTPRequestHandler` does not echo `Connection: close`, so the fixture was incapable of
reproducing what real servers do. Working through it with Kiro, the fix landed in both places:
the transport now parses responses with `HTTPResponse` over a socket it owns outright, and the
test fixture gained an `announce_close` option so it can behave like a real server. Correcting
the request line by hand also fixed a `Host` header that wrongly included a default port.
The lesson stuck: a green suite proves the tests pass, not that they are looking.

Kiro also ran the gate. `scripts/run_checks.py` is a single command covering lint, format,
strict types, and the whole suite, and it was run after every task rather than at the end.

## Attributions

Provenance is MIT licensed; see [LICENSE](LICENSE). Direct dependencies, all pinned:

| Package | Version | Licence | Used for |
| --- | --- | --- | --- |
| [Streamlit](https://streamlit.io) | 1.62.0 | Apache-2.0 | the local interface |
| [Pillow](https://python-pillow.org) | 12.3.0 | MIT-CMU | image decoding and PNG encoding |
| [NumPy](https://numpy.org) | 2.5.2 | BSD-3-Clause | pixel arrays and bit manipulation |
| [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) | 4.15.0 | MIT | HTML parsing for image discovery |
| [Requests](https://requests.readthedocs.io) | 2.34.2 | Apache-2.0 | request and URL preparation only, not transport |
| [python-whois](https://pypi.org/project/python-whois/) | 0.9.6 | MIT | pinned for planned WHOIS lookups, currently unused |

Development tooling: [pytest](https://pytest.org) 9.1.1,
[Hypothesis](https://hypothesis.readthedocs.io) 6.165.10,
[mypy](https://mypy-lang.org) 2.3.1, [Ruff](https://docs.astral.sh/ruff/) 0.16.3,
[Playwright](https://playwright.dev) 1.62.0 (pinned for planned browser tests).

Standards followed rather than libraries used: RFC 9110 for HTTP message syntax, RFC 9309 for
robots.txt, and the HTML Living Standard for `srcset` tokenization. Watermark integrity uses
CRC-32 through Python's standard-library `zlib.crc32`. Canonical JSON, canonical pixel
serialization, the watermark frame format, and the LSB embedding scheme are this project's own,
specified in [`design.md`](.kiro/specs/provenance/design.md).

The artwork in [`docs/`](docs/) and on the test page is the author's own original work. No
third-party images, datasets, or assets are included in this repository.

The dashboard's visual style is an original interpretation inspired by seventh-generation console
dashboards. No font, icon, image, sound, trademark, or other asset from any console manufacturer
is used, referenced, or distributed here. `provenance/ui/theme.css` is hand-written CSS over
system font stacks.

## Security notes

- The dashboard binds to `127.0.0.1` and has no authentication, because it is a
  single-user local tool with no network-exposed surface. Do not expose it to a network or put
  it behind a public reverse proxy; nothing in it is built to be multi-tenant or authenticated.
- No secrets, API keys, or credentials are required or stored. `.streamlit/secrets.toml` is
  gitignored and unused.
- Styling is the only markup in the app. `provenance/ui/theme.css` is a static asset handed to
  `st.html` as a `pathlib.Path`, never as a runtime string, so no value from a user, a file, or a
  scanned page can reach the document. Streamlit sanitizes it and ignores JavaScript by default,
  and this codebase never opts in. `tests/test_theme.py` asserts all of that, including that no
  other module inserts HTML and that the stylesheet fetches nothing.
- The registry lives outside the source tree and is never committed.
- Environment proxy variables are deliberately ignored, since a proxy would break address
  pinning and defeat the peer verification that prevents requests reaching private hosts.
- Scan only pages you are authorised to request. Provenance makes you confirm that before every
  scan, and it identifies itself in its user agent.

## For judges

Provenance is a local-first tool, so the demo is a local build rather than a hosted service.
Everything needed is free and in this repository.

1. Follow [Setup](#setup), then [Running it](#running-it). Clone, create a virtual environment,
   install pinned dependencies, run one launcher script.
2. **No login and no credentials.** There is no account system, so there is nothing to issue test credentials for.
3. Scan <https://joshrandomcodes.github.io/Provenance/>. That page is published from
   [`docs/`](docs/) in this repository and shows the author's own watermarked image presented
   as a print for sale. On a fresh machine this demonstrates discovery, download, and watermark
   extraction, and the image is reported as **Unregistered** with the detail
   `asset_hash_not_registered`. That is correct behaviour, not a bug: the registry is local, so
   your copy has never seen this asset. It is also the guarantee that matters most here, since
   a watermark alone is never treated as a match.
4. To see a **Verified** match, forge your own image and scan a page you control. Watermark any
   PNG or JPEG in The Forge, download the result, publish it somewhere public you are
   authorised to use, and scan that address. The registry entry created by the Forge is what
   turns the extracted payload into a verified match. That match then appears in **Incident
   Triage**, where the fair-use decision can be previewed and committed.
5. To confirm the tests pass rather than taking this README's word for it, run
   [Testing](#testing). One command, about two and a quarter minutes, no network access.

## Demo video

*Link to be added before submission.*
