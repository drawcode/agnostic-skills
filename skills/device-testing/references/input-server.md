# The in-app input server protocol

What an app implements so `device` can drive it on a platform with no host touch injection (iOS
before 27), and the same way on Android. Keep it out of release builds.

## Lifecycle

- **Off unless asked.** The app starts it only when launched with an environment variable (iOS)
  or a string intent extra (Android) whose value is the port, or `1` for the default. `device
  launch --input-server` passes `INPUT_SERVER_ENV_VAR` / `INPUT_SERVER_INTENT_EXTRA` from config.
- **Loopback only**, on `INPUT_SERVER_PORT` (default 11010). `device forward` bridges it over USB
  (`pymobiledevice3 usbmux forward` / `adb forward`), one Mac port per device. Off the LAN, so no
  network policy can block it and nothing else on the network can reach it.
- Requests are queued to the app's main thread and answered when the action has finished, so a
  swipe's response arrives after the finger lifts.

## Requests

Plain HTTP GET, JSON response `{"ok": true, ...}` or `{"ok": false, "error": "..."}`. Coordinates
are 0-1 from the top-left; `device` converts from screen pixels, so the app's render resolution
does not matter.

| Path | Does |
|---|---|
| `/info` | `screenWidth`, `screenHeight` (render size), `keyboardVisible`, `keyboardArea`, `selected` |
| `/tap?x&y[&ms]` | press for `ms` (default 60), one touch |
| `/swipe?x1&y1&x2&y2[&ms]` | press, a move per frame along the line, lift at `ms` (default 300) |
| `/text?value=` | set the focused input field's text directly, and the soft keyboard's |
| `/deselect` | clear focus, which closes the keyboard |
| `/key?text=` / `?special=delete[&count=]` / `?special=return` | type through the platform's text-input path, as a key press would (iOS) |
| `/keyboard` | native keyboard and appearance state: system style, focused field, input mode, window styles (iOS) |
| `/overlay/close` | optional: close the app's own debug overlay, e.g. a native log bar that an injected touch passes under |

## Implementation notes

- Inject into the **same input pipeline the app reads**, so a touch reaches UI and gestures alike.
  In a Unity app on TouchScript that is an `InputSource` queue: began on the first frame, a move per
  frame, ended at the duration.
- Do not touch the input system at startup if reading its singleton creates one; enable the
  injector on the first gesture, after the app's own has been made.
- Parse requests with a raw TCP listener rather than a framework that matches the `Host` header to
  a prefix; a port forward can change the header.
- On iOS a field copies its text back from the soft keyboard every frame, so `/text` sets both.
- A native return of a C++ `bool` must be marshalled as one byte (`MarshalAs(UnmanagedType.I1)`).

A project add-on's docs name its implementation.
