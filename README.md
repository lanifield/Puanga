# Puanga

Local-first replacement for the old Google Sheets + Apps Script time-keeping backend. Full design: [Time Keeping App Spec.md](../../Mahara/SYSTEM/Apps/Puanga/Time%20Keeping%20App%20Spec.md).

## Layout

- `service/` — FastAPI service. Writes `time-entries.json` to `G:\Mahara\SYSTEM\Apps\Puanga\time-entries.json` by default (override with the `PUANGA_DATA_FILE` env var).
- `pwa/` — phone front-end: start/stop UI, IndexedDB offline queue, sync against the service.
- `desktop/` — always-on-top desktop widget (tkinter), replacing the old `timekeeper_widget.pyw`. Talks to the same service directly over `http://127.0.0.1` since it's local.

## Running the service

**Always `cd` into this project first** — `G:\Programs\Puanga`, not `G:\` or anywhere else. Both `.venv\Scripts\Activate.ps1` and `--app-dir service` are relative to wherever you run them from, so activating or running from the wrong directory either fails outright or silently activates an unrelated `.venv` (if one happens to exist there too — same `(.venv)` prompt prefix either way, which is what makes this mistake easy to miss).

First time only — create a virtual environment and install dependencies into it:

```powershell
cd G:\Programs\Puanga
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r service/requirements.txt
```

Then, each time you want to run it:

```powershell
cd G:\Programs\Puanga
.venv\Scripts\Activate.ps1
uvicorn main:app --app-dir service --host 0.0.0.0 --port 8420
```

You'll see `(.venv)` in the prompt once activated. If `uvicorn` still isn't recognized after that, you're almost certainly not in `G:\Programs\Puanga` — check with `pwd` first.

If you'd rather not activate at all, this one-liner works from anywhere, no `cd` required, since every path in it is absolute:

```powershell
G:\Programs\Puanga\.venv\Scripts\uvicorn.exe main:app --app-dir G:\Programs\Puanga\service --host 0.0.0.0 --port 8420
```

## Running the desktop widget

The easiest way in is the **Puanga** shortcut on the Desktop — double-click it, or better, run it once and then right-click its taskbar icon and choose **Pin to taskbar**. From then on, that pinned icon both starts the app (if it isn't running — it checks for the service and starts that too, so you don't need a terminal open first) and brings the existing window forward (if it already is).

To run it manually instead:

```powershell
cd G:\Programs\Puanga
.venv\Scripts\python.exe desktop\widget.pyw
```

It starts hidden — click its taskbar icon to bring it up. Pick a Project/Course (Client fills in automatically from the vault) and enter a Task, hit START; it collapses to a compact view with a live timer until you hit STOP. It auto-hides ~3 seconds after you click away, same as the old widget.

Useful overrides (env vars): `PUANGA_SERVICE_URL` (default `http://127.0.0.1:8420`) and `PUANGA_WIDGET_DEBUG=1` (keeps the window visible on startup instead of iconifying — handy for checking position/sizing).

`desktop/icon.png` / `desktop/icon.ico` are the app icon (window/taskbar and the shortcut respectively) — replace both with a new image and everything picks it up, no code changes needed. Two Windows/Tk quirks this ran into, in case the icon ever needs redoing:

- **Use `.ico`, not `.png`, for the window itself.** Tk's `iconphoto()` reliably sets the title-bar icon but Windows' taskbar (big) icon often ignores it; `iconbitmap()` with a real multi-resolution `.ico` fixes both at once. `widget.pyw` falls back to `iconphoto` + the `.png` only if `icon.ico` is missing. To regenerate `icon.ico` from a new source image (`pip install Pillow` first, then uninstall it again afterwards — it's not a runtime dependency, just needed for this conversion):
  ```python
  from PIL import Image
  Image.open("desktop/icon.png").convert("RGBA").save(
      "desktop/icon.ico", format="ICO", sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)]
  )
  ```
- **Every `pythonw.exe`-hosted app looks the same to Windows unless told otherwise.** Without an explicit "Application User Model ID", Windows groups all `pythonw.exe` processes together and shows a generic icon on the taskbar button regardless of what the window sets. `widget.pyw` calls `SetCurrentProcessExplicitAppUserModelID("Puanga.TimeTracker")` at startup to give itself a distinct identity — that's what makes the taskbar icon actually show the stopwatch instead of a generic document icon.

## Project/Course and Client selects

Both front-ends populate the Project/Course dropdown from the vault: `GET /options` scans `Projects/*/_frontmatter.md` and `Courses/*/_frontmatter.md` under `G:\Mahara` (override with `PUANGA_VAULT_ROOT`), and only lists a folder if it has a `_frontmatter.md` with both a name (`project_name` / `course_name`) and a client (`client` for projects, `provider` for courses) filled in — see `service/vault.py`. Client isn't a separate choice: it's whatever the selected project/course declares, filled in automatically and not editable, so a time entry can't end up attributed to the wrong client by mistake.

**This means the dropdown is empty until real project/course folders exist with those fields filled in** — the placeholder `Projects/project-name/` and `Courses/course-name/` folders don't have a `_frontmatter.md` yet, so they won't show up. Add one for whatever you're actually tracking time against (built from `SYSTEM/Templates/project_frontmatter.md` or `course_overview.md`) before either front-end has anything to select.

`/entries/start` also validates `project_id` server-side against this same scan and rejects anything not currently listed (422) — the phone's offline queue is the one exception, since it can't reach the vault while offline and instead uses its last-fetched cache of options (`PWA → cached_options` in IndexedDB) to fill in the client before queuing.

## Running the PWA

Serve `pwa/` as static files (service workers require either HTTPS or `localhost`):

```bash
python -m http.server 8080 --directory pwa
```

Open the Settings panel on the phone and point "Local service URL" at wherever the FastAPI service is actually reachable.

## Two things to set up before the PWA works from a phone off `localhost`

1. **HTTPS for the service.** Browsers block an HTTPS page (required for the PWA's service worker) from calling a plain `http://` address — the request is silently dropped as mixed content. Generate a local cert with [mkcert](https://github.com/FiloSottile/mkcert), install its root CA on the phone once, then run uvicorn with `--ssl-keyfile` / `--ssl-certfile`.
2. **A stable address for the PC.** Set a DHCP reservation on the router for the PC's MAC address so its IP doesn't change, then point the PWA's "Local service URL" setting at `https://<that-ip>:8420`.

Neither is set up yet in this scaffold — the service runs over plain HTTP by default, which is fine for testing from a browser on the same machine, or from the desktop widget (no browser involved, so no mixed-content restriction), but won't work from a phone yet.

## What's already handled

- `client_id` (who the work is for), `project_id` and `task` are all rejected (422) if blank, rather than silently stored as `"undefined"`.
- `/entries/start` returns 409 if an entry is already running — no double-booking from a double-tap or a forgotten stop, whichever front-end it comes from.
- `/entries/sync` upserts by `id`, so a retried batch (e.g. after a dropped connection) can't duplicate entries.
- Sync is triggered on app-foreground/visibility-change plus a plain interval, not a background network event — works the same on iOS (no Background Sync API) and Android.
- The desktop widget checks the service for an already-running entry on startup, so it reflects reality (e.g. something started from the phone) instead of always assuming idle.
- The desktop widget starts the service itself if it isn't already running, so the pinned taskbar icon is a genuine one-click "start (or check on) the app."
