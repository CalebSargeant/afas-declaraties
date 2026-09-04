# Common mistakes / gotchas

Everything here was paid for once already. Symptom first, because the symptom is
what you will actually have in front of you.

---

## 1. `is_visible()` cannot tell you which Entra sign-in step is on screen

**Symptom.** The sign-in loop clicks `#idSIButton9` twice. The second click lands
on the *next* pane's primary button and submits an **empty password**. You get a
Dutch "voer uw wachtwoord in" style rejection, a bad-credential error, or a tick
on the account lockout counter, with nothing in the log that says why.

**Cause.** Entra is a single-page app that keeps **every pane in the DOM at
once**. On the password pane the email input is still `visibility: visible` with
a non-zero bounding box. It is merely parked in a corner, `aria-hidden` and
untabbable. So Playwright reports **both** `input[name='loginfmt']` and
`input[name='passwd']` as visible on **both** panes, and a step detector built on
`is_visible()` picks whichever branch it tests first, forever. The mirror-image
trap: waiting for a field to become *hidden* never succeeds either, because the
pane stays in the DOM. That wait times out, the loop re-detects the same step,
and clicks the shared submit again.

**Fix.** `entra._is_active()` / `_ACTIVE_JS`. An element is the one the user
would actually act on only if **all** of these hold:

- `el.offsetParent` is non-null (not `display:none`, not in a detached subtree),
- `el.closest('[aria-hidden="true"]')` is null,
- its `getBoundingClientRect()` has non-zero width and height,
- and for inputs only, `el.tabIndex >= 0`.

None of those depend on the interface language, which matters because the tenant
renders in Dutch. Transitions use `entra._await_inactive()`: wait for the
**current** control to stop being active, never for the next one to appear.

Do **not** apply the `tabIndex` rule to containers. The account picker
`#tilesHolder` is legitimately `tabIndex -1`, so a blanket rule makes the picker
undetectable and the state machine stalls in `state="waiting"` until the
deadline. That is what the `focusable=False` argument is for.

---

## 2. Entra ships `#passwordError` pre-populated but hidden

**Symptom.** Every healthy sign-in dies instantly with
`EntraLoginError: Entra rejected the sign-in: ...`, quoting text for a failure
that never happened.

**Cause.** Entra's panes carry their error containers in the markup with the text
already in them, hidden until needed. Testing for *text* alone reports a failure
on a perfectly good sign-in. `[role='alert']` is worse still: field-level hints
render there before you have typed anything.

**Fix.** `entra._raise_on_page_error()` iterates `SEL_ERROR` and `continue`s on
any handle whose `is_visible()` is false. Only a **visible** container with
non-empty text is a real error. Keep `SEL_ERROR` narrow
(`#passwordError, #usernameError, .alert-error`) and never widen it to
`[role='alert']`.

---

## 3. InSite renders TWO buttons labelled "Aanmaken"

**Symptom.** Confirming the first row files the whole declaration, so a
declaration goes in containing one day. Or the reverse: the loop thinks it is
creating the declaration and only closes a dialog.

**Cause.** The row dialog's confirm button and the page-level create button carry
**the same Dutch label**, and they do very different things:

| Element id | What it does |
|---|---|
| `Window_<n>_Actions_AntaUpdateCloseWebForm` | Confirms **one row** and closes its dialog |
| `Window_0_Actions_AntaUpdateCloseWebForm` | Creates the **whole declaration** |

**Fix.** Address both by id, never by role or label. `insite.ROW_CONFIRM_ID` and
`insite.CREATE_DRAFT_ID`. Any use of `get_by_role("button", name="Aanmaken")` is
a bug on sight.

`<n>` is a **per-page-load counter**, not a constant: it becomes 1 after the
first `Nieuw` and higher if any dialog opened before it. `insite._window_index()`
discovers it by walking open shadow roots for
`Window_(\d+)_Declaratie_HrZ50_`. Never hardcode `Window_1`. An index of `0`
after `Nieuw` means the row dialog did not open, which is a `PortalChanged`, not
something to retry into.

---

## 4. `Periode` defaults to the CURRENT month, not the booking date's month

**Symptom.** The worst kind: none. Every field looks right, the declaration files
cleanly, and August days filed on 28 August for a period that has already rolled
land in payroll period 9. You find out on a payslip.

**Cause.** A fresh row prefills `PeId` with the **current** period regardless of
`Datum boeking`, and editing the date afterwards does **not** update it.

**Fix.** Two halves, both required.

1. `insite.fill_row()` sets `PeId` explicitly on **every** row, and sets it
   *after* the date, because a later date edit can reset it.
2. `insite.verify_row()` re-reads the committed form and raises `InSiteError`
   unless `row["ref:Periode"] == str(line.period)`, with the year checked
   alongside it.

Never rely on the prefill, not even on a run that happens to be in the right
month. `ClaimLine.period` is `day.month` because this environment uses calendar
periods (confirmed against the period lookup, Januari=1 … Augustus=8); a
13-period calendar changes that one property and `store.period_for()`.

---

## 5. Each InSite field id exists TWICE, and the value lives in a different place per component

**Symptom.** Reading a field returns `None` or `""` and the form looks empty even
though the value is visibly on screen. Or a `fill()` appears to work and the
value silently reverts.

**Cause.** InSite renders controls as custom elements with an open shadow root.
`#Window_1_Declaratie_HrZ50_DaTi` matches **both** the custom-element host and
the inner `<input>` inside its shadow root. Worse, the value does not live in the
same place for every component type:

| Component | Where the value is |
|---|---|
| date / number (`DaTi`, `Qu`) | the **inner** `<input>`'s `value` **property** |
| `afas-reference` (`PeId`) | the **host**'s `value` **attribute** |

Read the wrong one and you get `None`, which reads exactly like an empty form.

**Fix.**

- Date/number: select `f"#Window_{w}_{TABLE}_{code} input"` (note the descendant
  ` input`) and use `fill()` / `input_value()`. That is `insite.set_text_field`,
  which writes, presses Tab, waits, reads back, and retries; InSite does async
  server round-trips that discard a value typed a moment earlier.
- References: click the **host** `f"#..._{code}_typeahead-base"` and pick an
  `afas-menu-item[value='<v>']`, then assert with `get_attribute("value")`. That
  is `insite.set_reference`. The inner combobox cannot be clicked, an overlay
  span legitimately intercepts pointer events, so typing into it is not an
  option.
- `insite._READ_ROW_JS` walks light DOM and open shadow roots together, keying
  plain inputs by `aria-label` and `afas-reference` hosts by `label` with a
  `ref:` prefix. That prefix is why `verify_row` looks up `ref:Periode` and plain
  `Datum boeking` / `Jaar` / `Aantal`.

`scripts/explore_insite.py` exists to dump this safely (read-only, never clicks a
submit control) when the portal moves.

---

## 6. `Aantal` is disabled on the woon-werk form

**Symptom.** A `fill()` on `Qu` times out or throws, and a retry loop burns its
attempts on a field that was never writable.

**Cause.** One row is one day's travel, both ways. The declaration profile fixes
the quantity at `1,00` and renders the field disabled. Other profiles may leave
it editable, so neither "always write" nor "never write" is correct.

**Fix.** `insite.set_or_verify_quantity()` checks `is_disabled()` **first**.

- Disabled: read the value back and raise `InSiteError` if it is not what the
  ledger intended. Filing anyway would claim a different amount than was
  recorded.
- Enabled: write it through `set_text_field` as normal.

Either way the number that will actually be filed is asserted before
`Aanmaken`. Never blind-write it and never skip the check.

---

## 7. `op` blocks forever locally when the 1Password desktop app is locked

**Symptom.** `onepassword.get_field()` hangs, then
`OnePasswordError: 'op item get ...' timed out after 30s`, with a valid service
account token and a clean environment. Nothing in `op`'s output explains it.

**Cause.** On a workstation the CLI talks to the desktop app over
`~/.config/op/op-daemon.sock` and waits on a biometric unlock that is never going
to arrive in a headless run. The service account token does not override this.

**Fix.** Set `OP_DOCKER_IMAGE=<PLACEHOLDER>` locally. `onepassword._command()`
then runs `op` **inside a container**, which has no desktop app to fall back to,
so the token is used directly. This is also exactly how the CLI runs in
production, so it is the faithful path rather than a workaround.

Two details that are not incidental:

- The token is passed **by name** (`-e OP_SERVICE_ACCOUNT_TOKEN`), never as an
  argv value. A container's argv is world-readable via `ps`.
- In the cluster `OP_DOCKER_IMAGE` stays **unset**: there is no docker socket,
  and the same 30-second timeout there means the service account token is
  missing or invalid, not that something is locked.

`get_totp()` is a second invocation on purpose (`--otp` and `--format json` are
mutually exclusive) and is called at the moment the code is typed, never up
front: the window is 30 seconds and preceding page loads eat most of it. That is
why `Credentials.totp` is a callable and not a string.

---

## 8. A degraded calendar read must NEVER be interpreted as "no office days"

**Symptom.** The most expensive failure in the system, and a completely silent
one: a whole month classified as working from home because OWA did not render,
lost its session, or changed its markup. Under-claiming looks like a quiet
success.

**Cause.** "No events found" and "the page did not load, or the label format
moved" are **indistinguishable** at the call site. Any code that returns a bare
list converts an outage into a month of wrong claims.

**Fix.** Three layers, all of them load-bearing.

1. `calendar_owa.read_week()` returns `(events, degraded)`. `degraded` is true
   when labels were harvested but **every** one failed to parse, which means the
   label format moved rather than the week being empty.
2. `classify.classify_day(..., calendar_degraded=True)` returns
   `Verdict.AMBIGUOUS` with `Reason.CALENDAR_DEGRADED`, and it does so **before**
   the booking rules, so nothing downstream can fall through to "no booking,
   therefore home".
3. `Verdict.AMBIGUOUS` maps to `DayState.NEEDS_INPUT`, which is excluded from
   `store.claimable_days()` and surfaces in `store.unresolved_days()` so a human
   is asked.

Never discard the second half of that tuple. Never wrap a calendar read in
`except: return []`. The regression test is
`tests/test_calendar_owa.py` plus `test_degraded_calendar_is_never_read_as_a_home_day`
in `tests/test_classify.py`; if you change the calendar path, that test is the
one that matters.

---

## Also worth knowing

- **`"microsoftonline.com" in url` is not a host check.** A `redirectUrl=` query
  parameter routinely carries the other side's domain, so the substring test
  reports "on the identity provider" while sitting on the application's own
  page. Use `entra.host_matches()`, which parses the hostname and accepts only
  an exact match or a subdomain.
- **Arriving on the right host is not the same as being signed in.**
  `session.wait_until_settled()` asserts the host **and** that the path is not
  one of `TRANSIENT_PATHS` (`/signin-oidc`, `/signin`, `/login`,
  `/authenticationhandler`). Dropping the second half lets a mid-handshake
  callback URL pass as authenticated; the next navigation is then bounced back to
  sign-in and the run fails somewhere unrelated.
- **The saved browser profile does not carry a session between runs** when the
  tenant suppresses "Stay signed in?". Entra issues a non-persistent cookie that
  dies with the browser process, so every run signs in afresh. That is why there
  is no session-keeper workload, and why `browser-profile/` is not a PVC.
- **`browser-profile/`, `traces/` and `artifacts/` are credentials, not caches.**
  A saved profile is a replayable, MFA-satisfied corporate session; a trace holds
  full DOM, headers and cookies. They are gitignored and must stay out of the
  Docker build context (`.dockerignore`). Never attach one to an issue.
- **Never retry a browser job.** `backoffLimit: 0` and
  `concurrencyPolicy: Forbid` everywhere. Retrying a corporate SSO login is how
  an account gets locked out, and `store.browser_lock()` yields `False` rather
  than blocking so a second job exits cleanly instead of queueing behind one
  stuck in an SSO flow.
- **`DRY_RUN` gates a path that spends real money.** It defaults to `"true"` in
  `values.yaml` and `insite.create_draft(dry_run=True)` returns without clicking.
  Flipping it is a deliberate act, never a side effect of another change.
- **This repo is public.** No employer name, InSite hostname, Entra tenant GUID,
  Slack workspace/channel/user id, OCI OCID, vault name, email address or
  employee number in any tracked file, including in a comment or a
  redacted-looking example. Use `<PLACEHOLDER>`.
