# rc-clean

A CLI that removes test customers from configured [RevenueCat](https://www.revenuecat.com/)
projects. It checks App Store and TestFlight releases for iOS, and Google Play
production and test tracks for Android. Customers last seen on RevenueCat's
Test Store are always candidates. Real purchasers are always kept.

## Classification rules

A customer is classified as test only when purchase and release data support
that decision.

For iOS, `rc-clean` compares RevenueCat app versions against App Store
submissions and TestFlight builds. Apps without a public release, pending
versions, TestFlight-only versions, sandbox-only purchases, and customers first
seen before the first public release are candidates.

For Android, it reads the Google Play production track and configured test
tracks. If there is no published production release, non-purchasers are
pre-release candidates. After production launch, only versions found on a
published test track and newer than production, sandbox-only customers, or non-purchasers selected by an
explicit `--created-before` cutoff are candidates. Unknown Android versions are
kept. Play release names in `versionName`, `versionName (versionCode)`, or
`versionCode (versionName)` form are matched to RevenueCat's app version. To
clean a pre-release cohort after launch, use `--created-before` with a cutoff
after that cohort. If a test-track
lookup is unavailable after production launch, versions from that track are
treated as unknown and kept. `--assume-unreleased` skips Play reads for a single
Android app run when its unreleased state is already confirmed. It requires
`--platform android` and does not persist in the app config.

**Anyone with a real purchase is always kept.**

## Install

```bash
git clone https://github.com/jackwallner/rc-clean.git
cd rc-clean
pip install pyjwt requests
```

## Configure

**1. Your app map**: copy the example and fill in your projects.

```bash
mkdir -p ~/.rc-clean
cp apps.example.json ~/.rc-clean/apps.json
```

```json
{
  "com.you.app": {
    "name": "Your App",
    "rc": "<ios-revenuecat-project-id>",
    "asc": "<app-store-connect-app-id>",
    "allow_versions": [],
    "android": {
      "name": "Your App Android",
      "rc": "<android-revenuecat-project-id>",
      "package_name": "com.you.app",
      "filter_platform": "android",
      "google_play_credentials": "~/.config/google-play/service-account.json",
      "test_tracks": ["internal", "alpha", "beta"],
      "allow_versions": []
    }
  }
}
```

Keep the existing top-level `rc` and `asc` fields for iOS. The nested
`android` object is optional. Android-only apps can omit the top-level `rc` and
`asc` fields. RevenueCat project ids are the short hashes in their dashboard
URLs. `package_name` must match the package in Google Play Console.

Android cleanup only considers customers whose `last_seen_platform` is
`android`. If one RevenueCat project serves both iOS and Android, set
`ios.filter_platform` to `iOS` in that app entry so the iOS cleaner skips
Android customers too. Nested `ios` fields override the top-level iOS fields.

`rc-clean` auto-detects the app by walking up to the nearest `project.yml`,
Xcode project, or Android Gradle project and matching its bundle id or package
name. Run it inside the app repo.

`allow_versions` is an optional list of app versions to always keep. Values are
canonicalized, so `1.3.0` and `1.3` match the same version. Android can use its
own nested `allow_versions` list. Add custom Google Play test track names to
`test_tracks`; the default list is `internal`, `alpha`, and `beta`.

**2. App Store Connect API key**: a shell-style file at
`~/.rc-clean/asc_credentials` exporting an App Store Connect API key with app
access.

```bash
export ASC_API_KEY_ID="XXXXXXXXXX"
export ASC_ISSUER_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export ASC_KEY_PATH="$HOME/.appstoreconnect/AuthKey_XXXXXXXXXX.p8"
```

**3. Google Play API access**: configure a local service-account JSON that
already has Android Publisher API access to the app. Keep it outside the repo,
then set `google_play_credentials` in that app's `android` config, or set
`RC_CLEAN_GOOGLE_PLAY_CREDS` to use one file for every Android app. The tool
uses the read-only Play production and track release list endpoints.

**4. RevenueCat auth**: the tool uses a RevenueCat dashboard session token.
Log in at [app.revenuecat.com](https://app.revenuecat.com), open DevTools, and
run:

```js
fetch('/v1/developers/login/refresh-token', {
  method: 'POST',
  headers: { 'X-Requested-With': 'XMLHttpRequest' },
  credentials: 'include'
}).then(r => r.json()).then(d => copy(d.authentication_token))
```

Seed it at `~/.revenuecat/auth_token`. It is stored with mode `600` and
self-refreshes thereafter:

```bash
./rc-clean-test-users --reseed <paste-token>
```

## Use

```bash
cd ~/my-app-repo
./rc-clean-test-users                         # dry run for configured platforms
./rc-clean-test-users --platform android      # Android only
./rc-clean-test-users --platform android --delete
./rc-clean-test-users --platform android --assume-unreleased --delete
./rc-clean-test-users --delete                # delete candidates for this app
./rc-clean-test-users --all-apps              # all configured apps/platforms
./rc-clean-test-users --all-apps --platform android
./rc-clean-test-users --check-coverage         # read-only account coverage check
./rc-clean-test-users --reset-qa               # delete the shared qa-ios/qa-android testers
```

Debug, simulator and emulator builds should configure RevenueCat with the fixed
app user ID `qa-ios` or `qa-android` instead of an anonymous one. Every test
install then reuses one customer, so test runs add at most one Active Customer
a day instead of one per install. The cleaner always treats those IDs as test,
and `--reset-qa` deletes them so the next run starts unpaid.

Dry run is the default; nothing is deleted until you pass `--delete`. For apps
configured for both platforms, the default scans both. `--platform` can
restrict a run to `ios`, `android`, or `all`.

Every all-platform `--all-apps` run checks the account's RevenueCat project
inventory. Missing projects or an unavailable inventory produce a nonzero exit
status while configured apps still run. Add new projects to the private app map
before considering the scheduled job healthy. Rejected customer deletions also
produce a nonzero exit status on either platform.
Apple connection failures, timeouts, throttling, and transient server errors
receive up to four attempts. Persistent failures remain errors; authentication
and permission errors are not retried.

For a project without accessible App Store Connect release metadata, configure
`{"rc": "<project-id>", "name": "Your App", "test_store_only": true}`.
This explicit mode removes only confirmed Test Store customers without real
purchases. It keeps every other customer and never guesses release status.

## Scheduled runs

Install `scripts/rc-clean-runner.py` and `scripts/rc-clean-cron.sh` in `~/bin`
alongside the cleaner. The shell entry point uses `~/.rc-clean/venv/bin/python`.
An existing launchd schedule can continue invoking `~/bin/rc-clean-cron.sh`.

The runner takes a kernel-managed lock at `~/.rc-clean/run.flock`. Crashed
processes leave no stale directory lock, and overlapping launches skip safely.
The child inherits the lock so an interrupted runner cannot permit an overlap
while cleanup is still active. Runs time out after three hours and terminate
their child process group. A successful run updates `last-success`; only a
successful full sweep advances `last-full-sweep`. The daily full sweep and
three-day incremental window retain the existing schedule behavior.

Logs remain in `~/Library/Logs/rc-clean.log`, including timestamped start and
exit markers for the existing watchdog. The runner supports `--script`,
`--state-dir`, `--log`, and `--timeout` for isolated verification.

Run reliability checks with `python -m unittest discover -s tests -v` in an
environment with the cleaner's dependencies installed.

## Configuration reference

| What | Default | Override |
| --- | --- | --- |
| App map | `~/.rc-clean/apps.json` | `$RC_CLEAN_APPS` |
| ASC credentials | `~/.rc-clean/asc_credentials` | `$RC_CLEAN_ASC_CREDS` |
| Google Play credentials | Per-app `google_play_credentials` | `$RC_CLEAN_GOOGLE_PLAY_CREDS` or `$GOOGLE_APPLICATION_CREDENTIALS` |
| RevenueCat token | `~/.revenuecat/auth_token` | `$RC_CLEAN_TOKEN` |

## Notes

- RevenueCat deletion is asynchronous. A deleted customer may still appear in
  the enumeration index briefly. Re-running is safe and idempotent.
- Customer rows whose RevenueCat detail endpoint returns 404 are reported as
  stale and skipped. They are already absent from the customer detail endpoint.
- Android release versions are matched by numeric Play release names. Unknown
  Android versions are kept after production launch, so custom Play release
  names cannot cause customers to be deleted by mistake.

## License

MIT
