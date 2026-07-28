# rc-clean

A small CLI that removes **test / TestFlight / simulator** customers from your
[RevenueCat](https://www.revenuecat.com/) projects without ever touching a real
paying user. Sandbox and TestFlight builds create anonymous RevenueCat customers
that pollute your New-Customer charts and cohort analytics; this tool finds and
deletes exactly those, and leaves everyone else alone.

## The one rule it trusts

A customer is **test** only if you can *prove* they are not a real App Store
buyer. `rc-clean` pairs each RevenueCat customer's last-seen app version against
Apple's App Store submissions **and** TestFlight build inventory:

- The app has **never been released** → any customer must be TestFlight/sim.
- The customer's last-seen version was **never `READY_FOR_SALE`** — including
  TF-only marketing versions (from ASC builds) and versions still in review /
  prepare → test.
- Sandbox-only purchasers (no real App Store purchase) → test.
- **Anyone with a real (non-sandbox) purchase is never deleted**, full stop.

Because it re-checks Apple's live release states and TF builds on every run, it
self-corrects as builds get approved. It deliberately does **not** use the
tempting "first-seen before the release date" heuristic: RevenueCat only
exposes *last-seen* version, not install version or build number, so that would
wrongly flag loyal users who installed early and updated to the latest build.

## Install

```bash
git clone https://github.com/jackwallner/rc-clean.git
cd rc-clean
pip install pyjwt requests
```

## Configure

**1. Your app map** — copy the example and fill in your projects:

```bash
mkdir -p ~/.rc-clean
cp apps.example.json ~/.rc-clean/apps.json
```

```json
{
  "com.you.app": {
    "rc":   "<revenuecat-project-id>",
    "asc":  "<app-store-connect-app-id>",
    "name": "Your App"
  }
}
```

The RevenueCat project id is the short hash in your dashboard URL; the ASC app
id is the numeric id in your App Store Connect app URL. `rc-clean` auto-detects
which app you mean by walking up from the current directory to the nearest
`project.yml` / Xcode project and matching its bundle id — so just run it from
inside an app repo.

**2. App Store Connect API key** — a shell-style file at
`~/.rc-clean/asc_credentials` exporting an App Store Connect API key with app
access:

```bash
export ASC_API_KEY_ID="XXXXXXXXXX"
export ASC_ISSUER_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export ASC_KEY_PATH="$HOME/.appstoreconnect/AuthKey_XXXXXXXXXX.p8"
```

**3. RevenueCat auth** — the tool uses a RevenueCat dashboard session token
(the public REST API can't list or delete customers). Log in at
[app.revenuecat.com](https://app.revenuecat.com), open DevTools, and run:

```js
fetch('/v1/developers/login/refresh-token', {
  method: 'POST',
  headers: { 'X-Requested-With': 'XMLHttpRequest' },
  credentials: 'include'
}).then(r => r.json()).then(d => copy(d.authentication_token))
```

Then seed it (stored at `~/.revenuecat/auth_token`, `chmod 600`, and
self-refreshing thereafter):

```bash
./rc-clean-test-users --reseed <paste-token>
```

## Use

```bash
cd ~/my-app-repo
./rc-clean-test-users            # dry run: list what WOULD be deleted
./rc-clean-test-users --delete   # actually delete
./rc-clean-test-users --all-apps # every app in your config (dry run unless --delete)
```

Dry run is the default; nothing is deleted until you pass `--delete`.

### Example

```
=== My App  (RC a1b2c3d4)  bundle com.you.app ===
released versions: NONE — app not public
scanning 12 customers...
  real (kept): 0   purchasers (kept): 0   TEST: 12
  test by version: (no detail)×5, 1.0.0×7
  (dry run — pass --delete to remove these 12)
```

## Configuration reference

| What | Default | Override |
| --- | --- | --- |
| App map | `~/.rc-clean/apps.json` | `$RC_CLEAN_APPS` |
| ASC creds | `~/.rc-clean/asc_credentials` | `$RC_CLEAN_ASC_CREDS` |
| RC token | `~/.revenuecat/auth_token` | `$RC_CLEAN_TOKEN` |

## Notes

- RevenueCat deletion is asynchronous; a deleted customer 404s within minutes
  but may still appear in the enumeration index briefly. Re-running is safe and
  idempotent.
- Ghost/anonymous records whose detail endpoint 404s are treated as test **only**
  when the app is pre-release (they cannot be real buyers of an unreleased app).

## License

MIT — see [LICENSE](LICENSE).
