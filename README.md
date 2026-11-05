# Hyundai Bluelink JP

Japan-only Hyundai Bluelink integration for Home Assistant, maintained separately
from the multi-region project. The Japan API is included and released with the
integration. No separate API package or local build is needed.

This starts from the full working Japan build, including vehicle status, lock and
unlock, climate, charging controls and limits, charge port, supported windows,
forced refresh, SVM images, command status watching and redacted diagnostics.
Availability depends on the vehicle and its Bluelink subscription. Commands can
take a while, as they do in the Bluelink app; avoid pressing repeatedly while a
command is still running.

Previously called **HyundaiJP**. Updates keep your existing account, entities,
services and automations; the repository URL and `hyundai_jp` domain stay the same.

## Install with HACS

Requires Home Assistant 2026.9.2 or newer and HACS.

1. Make a Home Assistant backup. Keep the existing Hyundai / Kia Connect
   integration installed for rollback, but **disable its config entry** before
   setting up Hyundai Bluelink JP so both integrations do not poll the same car.
2. Open **HACS → ⋮ → Custom repositories**. Add
   `https://github.com/phillip9933/HyundaiJP` and choose **Integration**.
3. Find **Hyundai Bluelink JP** in HACS and download the latest numbered release
   (`v0.1.0` or newer). For optional beta releases, enable **Show beta versions**
   in its HACS menu, then select the beta version in **Download / Redownload**.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration**, search for
   **Hyundai Bluelink JP**, and enter your Japan Bluelink username, password and PIN.

Hyundai Bluelink JP uses its own `hyundai_jp` domain. This is a one-time new setup; it does
not import or overwrite the old integration's account, entity registry or
automations. Check entity names before moving dashboard cards or automations to
the new integration. Do not remove the old entry until you are happy with the new
one.

Future releases appear in HACS. Install the update and restart Home Assistant;
there is no need to upload another ZIP. HACS may take time to discover a release;
use **Update information** on the repo if needed.

If a previous install failed with a download URL containing
`releases/download/main/hyundai_jp.zip`, use **Update information**, then select
**Download / Redownload → v0.1.0** (or newer). `main` is a source branch, not a
release asset. This happened when only the first beta release was available.

[HACS custom repo instructions](https://hacs.xyz/docs/faq/custom_repositories/)
· [HACS downloads and version selection](https://hacs.xyz/docs/use/repositories/dashboard/)

## Actions and settings

Schedule controls change only the requested setting. In service calls, leave
unrelated fields out to preserve them. The departure switches affect their own
slot; the service's top-level climate settings deliberately apply to both slots.
Window services can target only the supported windows you want to change.

Actions reject missing or invalid settings before sending a command. If a
multi-part schedule write reports partial acceptance, check Bluelink before
trying again: an earlier part may already have been accepted. Commands are not
automatically replayed. A failed force refresh reports an error rather than
treating cached status as confirmation of a fresh update.

Location, history and address lookup are optional. Temporary failures retain
the last available values while valid core vehicle status continues to update.
Reauthentication and reconfiguration keep the same account; add a separate
integration entry to use a different account.

### Charge port and command progress

Use the **Charge Port** button for a single action: open when the last reported
position is closed, or close when it is open. HA labels this action **Press**.
If the position is unknown, refresh status first; the button does not guess.

The **Charge Port Status** cover keeps Open, Closed, Opening and Closing states
and explicit Open/Close actions under Configuration. Its door icon changes with
the state. Opening/Closing means
the request is awaiting fresh car status; it does not claim the motor has
already moved. Locks similarly show Locking/Unlocking while confirmation is
pending. Old cached values do not clear this progress, and pending requests
expire rather than remaining stuck indefinitely.

The **Charge Port (legacy)** switch keeps its existing ID for automations. On an
existing dashboard, select the Charge Port button for the single action and the
Charge Port Status cover for status. There is no need to remove the account or
recreate automations. The old switch still follows telemetry, so it can appear in
Activity even when a different control sent the request. If no dashboard or
automation uses it, you can disable that legacy entity in its settings. Hiding
it alone does not stop its state changes from appearing in Activity.

Climate requests use current heater/defrost settings and the target selected in
the climate card. Home Assistant converts your chosen display units to the
vehicle's Celsius command units. If the car has not reported a target, choose
one and start from **Climate Control**; the integration does not invent a target.
Changing temperature while climate is running validates the whole request before
stopping and restarting it. Window Open, Close and Vent remain available; the
misleading Stop action that previously sent Close is no longer offered.

## Test and roll back

Start by checking vehicle status against Bluelink. Then try one command at a time
and wait for the result. For SVM, press **Capture SVM Image**, allow the car time
to capture, then open the SVM image entities. Add them to a dashboard if you want
the images visible there.

For troubleshooting, download diagnostics from **Settings → Devices & services
→ Hyundai Bluelink JP → your vehicle → Download diagnostics**. Review the file before
sharing it. Do not post account credentials, PINs or unredacted vehicle data.

To return to the old build, disable Hyundai Bluelink JP and re-enable the old Hyundai / Kia
Connect entry. Its repo, files and existing releases are unchanged. Within
Hyundai Bluelink JP, HACS **Redownload** also lets you select an earlier published version;
restart after changing versions.

## Development and releases

Vehicle communication lives in `custom_components/hyundai_jp/_api`. Home Assistant
entities and the coordinator live in its parent directory. Other regions are not
included. Keep shared helpers that Japan uses and test them alongside the Japan
adapter.

Each account stores its typed coordinator in `ConfigEntry.runtime_data`.
Services register once and route only to loaded accounts. The coordinator owns
polling, command serialization and shutdown. `svm_cache.py`
keeps camera captures and rendered views locally, using the coordinator’s tracked
executor. In the bundled API, `client.py` connects the authentication, commands,
SVM and telemetry components. `authentication.py` handles login and HTTP response
envelopes, `http.py` owns the bounded HTTP session, `options.py` holds request
models, and `telemetry.py` parses both Japan status formats.

For local development, use Python 3.14.8 and run
`python scripts/install_dependencies.py` in a virtual environment. This installs
the minimum supported HA version; use `--homeassistant 2026.11.0` to select the
current tested version. Run `ruff check .`, `ruff format --check .`, `mypy` and
`pytest` before submitting changes.

GitHub runs the same checks against HA 2026.9.2 and 2026.11.0, plus package
validation. Branch-aware coverage reports cover the whole component and are
saved as workflow artifacts, with an 80% combined coverage floor. Builds contain the integration and its API together. GitHub Actions
does not use a Bluelink account or send vehicle commands. Real vehicle testing is
done separately by the owner.

Use the repository's **Release** workflow to publish a new version. Regular
releases are the default so HACS can install without beta versions enabled;
select **prerelease** explicitly when publishing a beta. Each release
has a HACS-ready `hyundai_jp.zip`; earlier releases are retained for rollback.
See [the workflows](.github/workflows) for exact checks and release steps.

## Credits

The Hyundai Bluelink JP icon is maintained as [an SVG](custom_components/hyundai_jp/brand/icon.svg).
The 256px and 512px PNG icons are rendered from that source for Home Assistant.
Its Hyundai emblem comes from [Simple Icons](https://github.com/simple-icons/simple-icons/blob/develop/icons/hyundai.svg)
under [CC0](https://github.com/simple-icons/simple-icons/blob/develop/LICENSE.md).

Based on [kia_uvo](https://github.com/Hyundai-Kia-Connect/kia_uvo) and
[hyundai_kia_connect_api](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api),
with Japan work continued from
[kia_uvo_JP](https://github.com/phillip9933/kia_uvo_JP) and
[hyundai_kia_connect_api_JP](https://github.com/phillip9933/hyundai_kia_connect_api_JP).
The exact starting commits are recorded in [ORIGINS.json](ORIGINS.json).

Original MIT licenses are preserved in [LICENSE](LICENSE) and
[LICENSE.api.txt](LICENSE.api.txt). This is an unofficial integration and is not
affiliated with Hyundai.
