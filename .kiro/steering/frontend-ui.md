# Working on the Provenance interface

Read this before changing anything. It exists because the app's selling point is that it
is provably local, and the interface is the one layer where that can be broken by
accident.

## The terminal rule

**Never execute a terminal command.** Do not run git, pytest, mypy, ruff, pip, or the app.

Instead, print the exact command in a fenced code block for the human to copy and run.
Their shell is `cmd.exe` on Windows, not PowerShell, so `;` is not a command separator:
give **one command per code block**. Wait for them to paste the output before continuing.

## What you may change

- `provenance/ui/theme.css` and `.streamlit/config.toml` for palette, type, spacing, motion
- `provenance/ui/*_view.py` for layout, widget choice, ordering, and wording
- `provenance/ui/*_presenter.py` for labels and view models
- `provenance/ui/theme.py`, `safe_render.py`, `messages.py` when the change genuinely needs it

## What you must not change

Everything under `provenance/domain/`, `provenance/application/`, `provenance/infrastructure/`,
and `provenance/ports/`. Those hold identity hashing, the watermark engine, the SSRF-safe
network stack, the SQLite registry, and the triage decision logic. They are finished, verified
against a live page, and out of scope for interface work. If a UI change seems to require
touching them, stop and say so rather than doing it.

## Languages

Python and CSS. That is the whole surface.

No TypeScript, no JavaScript, no JSX, no React, no build step, no bundler. If a change would
introduce a file in another language, it is out of scope.

Be accurate about why, because the usual counter-argument is half right. Installing a Streamlit
custom component does not require Node or a build step: the package ships a pre-built bundle and
your local server serves it. The reasons this project still says no are different ones.

First, the security posture is the product. Today the app can claim it executes no JavaScript,
renders untrusted values as text through one helper, and contains one static stylesheet as its
only markup, and it points at tests that prove it. A component replaces that with "we vetted a
third-party bundle", which is a much weaker sentence in a README and a much weaker answer to
someone reviewing the project.

Second, some components fetch web fonts, icon sets, or animation data at render time, which
would make the README's no-outbound-requests claim flatly false rather than merely weaker.

Third, third-party widgets are routinely worse than native Streamlit at keyboard navigation,
focus handling, and ARIA, and this project has an accessibility requirement group to satisfy.

The project has also chosen to look the same in every browser, so avoid anything that renders
differently across engines unless it sits behind an `@supports` guard and its absence changes
nothing but decoration.

## Adding a library

Default answer: no, and it is very unlikely to be the right tool. Read the section below first,
because the thing people usually reach for a library to get here is animation, and animation
needs no library at all.

**Hard blocks.** Never install a package that:

- ships a bundled JavaScript or TypeScript frontend, which covers virtually every Streamlit
  custom component, including the shadcn, Ant Design, option-menu, elements, and aggrid families
- fetches anything at render time, which covers Lottie-style animation loaders and anything
  pulling web fonts or icon sets
- needs `unsafe_allow_html=True` or `st.html` with a runtime string to work

**If a package is genuinely pure Python** with no bundled frontend, no network access, and no
markup requirement, it is arguable rather than forbidden. Before proposing one, state plainly:
what it does that Streamlit cannot, what it pulls in transitively, whether it ships type hints,
and what it would cost. Adding one means all of this, in the same change: an exact `==` pin in
`pyproject.toml`, a re-freeze of `requirements.txt`, an attribution row in the README's table
(a hackathon rule, not optional), a mypy override if it is untyped, and a green gate. Then the
human decides. Never install anything without explicit approval.

Weigh the clock too. The submission locks on 23 August 2026, and a dependency added late is a
new failure mode in the setup instructions judges will follow on a machine you have never seen.

## What you can do without a library

The ceiling here is higher than it looks, and nothing below needs a single new package.

**All of CSS**, in `provenance/ui/theme.css`: keyframes, transitions, transforms, staggered
delays via `nth-of-type`, `clip-path` and mask reveals, gradients, `filter` and `backdrop-filter`,
blend modes, `@supports` guards, container and media queries, and scroll-driven animations
through `animation-timeline: view()` where the browser supports them. The existing stylesheet
already uses about half of these; the drifting background, the stepped typing reveal, the
breathing sidebar, and the travelling highlight are all plain CSS.

**Streamlit's own widgets**, which are richer than the app currently uses: `st.dialog` for modal
confirmations, `st.segmented_control` and `st.pills` for view switching, `st.status` for staged
progress, `st.toast` for transient feedback, `st.metric` with deltas, `st.badge` for statuses,
`st.dataframe` with `column_config` and row selection, `st.expander`, bordered containers,
columns, `st.progress`, and `st.spinner`.

**Streamlit's theme config** in `.streamlit/config.toml`, which is doing a lot of work already:
palette, link and code colours, border colours, radii, base font size and weight, separate
heading and code font stacks, per-sidebar and per-mode overrides, and chart palettes.

**Charts and figures, with no new dependency.** Streamlit bundles Altair, so `st.bar_chart`,
`st.line_chart`, `st.area_chart`, `st.scatter_chart`, and `st.altair_chart` are already
available, and `chartCategoricalColors` in the theme config keeps them on palette. Useful if the
scan summary or the incident list should read as figures rather than rows. Two cautions: a chart
label or axis value must never come from a scanned page, and a chart must never be the only
place a fact appears, because it is not readable by assistive technology on its own. Pair every
figure with the same numbers in text.

Two things genuinely need JavaScript and are therefore out of reach: numbers that count up, and
effects that measure text or the viewport at runtime. Say so plainly if asked for either, and
offer the closest CSS equivalent instead.

## Three invariants that must survive every change

**1. Nothing may be fetched from the network.** No `@import`, no `url()`, no web font, no
remote image, no CDN, no component that loads assets at render time. The README promises the
app makes no request except to a page the user names. `tests/test_theme.py` enforces this on
the stylesheet.

**2. No JavaScript, and no markup built at runtime.** The only markup in the app is
`provenance/ui/theme.css`, passed to `st.html` as a `pathlib.Path` by `apply_theme()`. Never
pass a string to `st.html`, never use `unsafe_allow_html=True`, never set
`unsafe_allow_javascript`. Tests assert that `st.html` appears in exactly one module and that
no module enables either flag.

**3. Untrusted values render as text, always.** Page titles, headings, captions, alt text,
commerce wording, filenames, and fair-use rationales come from files and scanned pages. They
go through `provenance/ui/safe_render.py` and nowhere else. Style the chrome, never the
evidence. A scraped caption must never be able to become markup, a link, or an image request.

To style a specific region, wrap it in `st.container(key="prov-something")` and target the
`st-key-prov-something` class Streamlit generates. That is the sanctioned hook.

## Motion and accessibility limits

- No flashing. Keep any cycle at or below about one hertz, well under the three-per-second
  threshold. Slow pulses, not blinks.
- Every animation must stop under `@media (prefers-reduced-motion: reduce)`. The block at the
  end of `theme.css` is where that goes, and it must stay last.
- A reveal's hidden state belongs in `@keyframes`, never in the rule. With animation switched
  off, a clip written into a rule hides the text permanently. A test enforces this.
- Keep the `:focus-visible` ring. It is the only focus indicator in the app.
- Every widget keeps a visible, persistent label. No label-less icon buttons.
- Styling hooks on `data-testid` and `data-baseweb` attributes are not a public Streamlit API.
  Never let meaning depend on them: if a selector stops matching, the app must still state
  every fact in text.

## Keep labels and docs in step

Control labels are asserted in `tests/test_app_smoke.py` and quoted in `README.md`'s usage
section. If you rename a button, a tab, or a caption, update the test and the README in the
same change. Never make the app claim a capability it does not have: strike authorization,
credit requests, deletion, and diagnostic logging are unimplemented and must stay described
that way. `.kiro/specs/provenance/tasks.md` records what is done and why.

## Verifying

Hand these to the human, in this order, before any commit.

```
.venv\Scripts\python.exe -m ruff format .
```

```
.venv\Scripts\python.exe scripts\run_checks.py
```

The gate covers lint, format, strict mypy, and the full suite. It must pass with zero failures.
The expected baseline is 931 tests. A CSS-only change still needs the gate run, because
`tests/test_theme.py` reads the stylesheet.

Visual changes also need a human's eyes:

```
.venv\Scripts\python.exe scripts\run_app.py
```

## Git

Work on a branch, never commit straight to `main`.

```
git checkout -b ui/<short-name>
```

Stage specific files by name rather than `git add -A`. Ask before committing, and never force
push, reset hard, or amend a pushed commit.

## Two hard constraints from the hackathon rules

The submission locks at **23 August 2026, 23:59 UTC**. Nothing may change after that.

Ship no third-party asset. No font file, icon set, image, sound, trademark, or CSS lifted from
another project or product. The current look is an original interpretation inspired by
seventh-generation console dashboards and contains nothing from any console manufacturer. Keep
it that way, and if you add a visual idea from elsewhere, write it from scratch and say where
the inspiration came from.
