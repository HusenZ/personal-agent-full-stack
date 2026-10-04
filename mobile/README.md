# Personal Intelligence — mobile

A Flutter client for Android and iOS. It consumes the backend's REST API and
SSE stream and holds no business logic of its own: grounding, retrieval and
memory decisions all happen on the server.

## Why use it

What sets it apart from a general chatbot. Each point is something the backend
actually does, and the app makes it visible:

1. **It remembers you.** Goals, routines and preferences carry into every
   conversation. The Today tab shows how much it knows and what it learned
   recently.
2. **It shows its work.** Under each reply, a receipt says what the reply drew
   on (memories, your timeline, exact totals) and whether it passed
   verification. Tap it for a step-by-step "How I answered".
3. **Facts, not guesses.** Totals are counted from your records, never
   estimated. The analysis labels every point as fact, observation,
   inference, recommendation or uncertainty.
4. **You stay in control.** Review, correct, archive or delete any memory;
   delete any conversation; delete the whole account from the app.

The welcome screen walks new users through these four points before asking for
an account, and they stay available under You › How it works.

## What it does

- **Welcome**: the four reasons above, then sign up or sign in.
- **Today** (home): a greeting; an "Ask anything" entry with quick prompts; a
  "How well I know you" summary (interview progress, memory, goal and chat
  counts); your focus from the latest analysis; your goals as check-in cards;
  recently learned memories; and the last conversation to pick up.
  Suggestions open a *draft* in the composer, never a message sent for you.
- **Week plan**: "Plan my week" builds the next seven days from your goals and
  around your constraints, shown as a calendar: a day strip, each day's blocks
  on a timeline with times, what each block is for and why, linked to the
  goal or constraint it rests on, and ticks as the week goes. Today shows the
  day's slice of the plan.
- **Add to my calendar**: puts the plan's blocks on a calendar you choose on
  the phone (Google, iCloud, Outlook…), for one day, the rest of the week or
  all seven days, with an optional 10-minute reminder. Each event carries a
  hidden marker, so adding again skips blocks already there. Calendar
  permission is asked for only when you tap it.
- **Onboarding**: the adaptive interview, one question at a time.
- **Chat**: conversations grouped by recency, streamed turns showing what the
  assistant is doing, per-reply receipts, and swipe-to-delete or deleting
  from the menu.
- **Memory**: everything the assistant remembers, searchable and filterable by
  kind, with correct, archive (undoable) and permanent delete.
- **You**: profile, analysis, "How it works", sign out, and delete account
  (asks for the password).

## Requirements

- Flutter 3.47 (stable), Dart 3.13
- Android Studio / Android SDK for Android, Xcode for iOS
- The backend running (see the repository's README)

## Run

```bash
cd mobile
flutter pub get
flutter run                                   # debug: backend on this machine
flutter run --dart-define=API_BASE_URL=https://api.example.com
```

### Reaching a backend on your computer

A debug build starts on the right default for an emulator, and has a
**Server** chip at the bottom of the sign-in screen to switch addresses. The
choice is remembered across restarts; changing it signs you out, because tokens
from one server mean nothing to another. Use **Test** to check the address
answers before switching.

| Where the app runs                 | Address                     | Backend started with      |
| ---------------------------------- | --------------------------- | ------------------------- |
| Android emulator (default)         | `http://10.0.2.2:8000`      | `uvicorn app.main:app`    |
| Genymotion                         | `http://10.0.3.2:8000`      | `uvicorn app.main:app`    |
| iOS simulator (default on iOS)     | `http://localhost:8000`     | `uvicorn app.main:app`    |
| Android phone over USB             | `http://localhost:8000`, after `adb reverse tcp:8000 tcp:8000` | `uvicorn app.main:app` |
| Any phone on the same Wi-Fi        | `http://<your LAN IP>:8000` | `... --host 0.0.0.0`      |

- `10.0.2.2` is the Android emulator's alias for the machine running it;
  `localhost` inside the emulator is the emulator itself.
- `adb reverse` has to be run again after the device reconnects or the emulator
  restarts.
- Your LAN IP: `ipconfig getifaddr en0` on macOS, `hostname -I` on Linux,
  `ipconfig` on Windows. A firewall may need to allow port 8000.
- iOS asks for **Local Network** permission the first time a debug build
  reaches a LAN address; allow it, or re-enable it under Settings › Privacy ›
  Local Network.

Plain HTTP is only allowed in debug builds (Android's debug manifest, and iOS's
local-networking exception). Release builds refuse to start without
`API_BASE_URL`, accept HTTPS only, and never show the server picker.

## Checks

```bash
flutter analyze        # strict casts, inference and raw types
flutter test           # unit and widget tests; contract tests are skipped
dart format --output none --set-exit-if-changed lib test

# Contract tests: the app's client code against a real backend.
CONTRACT_API_BASE_URL=http://localhost:8000 flutter test test/contract
```

## Structure

```text
lib/
├── main.dart                 ProviderScope; automatic provider retries off
├── app/                      configuration, router (session-driven redirects), theme
├── core/
│   ├── api/                  API client, error type, SSE parser
│   ├── auth/                 token storage, session state
│   └── ui/                   shared components, error views and formatting
└── features/                 one folder per area: auth, onboarding, chat,
                              memories, profile, analysis, home
```

State is Riverpod 3, navigation go_router, HTTP the `http` package. Models are
hand-written `fromJson` constructors, so there is no code generation step.

## Decisions worth knowing

- **Token renewal is single-flight.** Refresh tokens rotate and the server
  revokes the whole session when one is presented twice, so however many
  requests find the access token expired at once, exactly one refresh is sent.
  A test fires five concurrent requests and asserts one refresh.
- **Offline is not signed out.** A network failure during renewal leaves the
  session intact; only the server refusing the refresh token ends it.
- **Errors are shown by code, never by server text.** `ApiException.userMessage`
  maps the backend's stable error codes to wording; unknown codes get a generic
  message.
- **Progress events never contain reply text** — the server guarantees it, and
  the app only maps event names to a stage label. Unknown event names are
  ignored, so the server can add events without breaking installed apps.
- **A failed turn can be retried safely**: the server stores nothing for a turn
  that failed or that the client abandoned (leaving the screen closes the
  stream).
- **Riverpod's automatic retry is off.** Every failure surfaces with its own
  retry; silent retries would repeat rate-limited, paid model calls.
- **Android:** `INTERNET` is declared in the main manifest (the template only
  declares it for debug, which would leave release builds offline); backups are
  disabled; cleartext HTTP is enabled by the debug manifest only.
- **iOS:** App Transport Security allows plain HTTP to local addresses only.

## Not yet done

- No Android or iOS build has been produced from this environment (the Android
  SDK could not be downloaded here, and iOS builds need macOS). The app is
  verified by analysis, unit and widget tests, and contract tests against the
  real backend.
- No push notifications, offline cache, or message search.
- English only; no localisation.
