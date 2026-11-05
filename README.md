# HyundaiJP

Japan-only Hyundai Bluelink integration for Home Assistant, maintained separately
from the multi-region project. The Japan API is included and released with the
integration. No separate API package or local build is needed.

This starts from the full working Japan build, including vehicle status, lock and
unlock, climate, charging controls and limits, charge port, supported windows,
forced refresh, SVM images, command status watching and redacted diagnostics.
Availability depends on the vehicle and its Bluelink subscription. Commands can
take a while, as they do in the Bluelink app; avoid pressing repeatedly while a
command is still running.

## Install with HACS

Requires Home Assistant 2026.9.2 or newer and HACS.

1. Make a Home Assistant backup. Keep the existing Hyundai / Kia Connect
   integration installed for rollback, but **disable its config entry** before
   setting up HyundaiJP so both integrations do not poll the same car.
2. Open **HACS → ⋮ → Custom repositories**. Add
   `https://github.com/phillip9933/HyundaiJP` and choose **Integration**.
3. Find **HyundaiJP** in HACS and download a release. For a beta release, enable
   **Show beta versions** in its HACS menu, then select the beta version in
   **Download / Redownload**.
4. Restart Home Assistant.
5. Open **Settings → Devices & services → Add integration**, search for
   **HyundaiJP**, and enter your Japan Bluelink username, password and PIN.

HyundaiJP uses its own `hyundai_jp` domain. This is a one-time new setup; it does
not import or overwrite the old integration's account, entity registry or
automations. Check entity names before moving dashboard cards or automations to
the new integration. Do not remove the old entry until you are happy with the new
one.

Future releases appear in HACS. Install the update and restart Home Assistant;
there is no need to upload another ZIP. HACS may take time to discover a release;
use **Update information** on the repo if needed.

[HACS custom repo instructions](https://hacs.xyz/docs/faq/custom_repositories/)
· [HACS downloads and version selection](https://hacs.xyz/docs/use/repositories/dashboard/)

## Test and roll back

Start by checking vehicle status against Bluelink. Then try one command at a time
and wait for the result. For SVM, press **Capture SVM Image**, allow the car time
to capture, then open the SVM image entities. Add them to a dashboard if you want
the images visible there.

For troubleshooting, download diagnostics from **Settings → Devices & services
→ HyundaiJP → your vehicle → Download diagnostics**. Review the file before
sharing it. Do not post account credentials, PINs or unredacted vehicle data.

To return to the old build, disable HyundaiJP and re-enable the old Hyundai / Kia
Connect entry. Its repo, files and existing releases are unchanged. Within
HyundaiJP, HACS **Redownload** also lets you select an earlier published version;
restart after changing versions.

## Development and releases

Vehicle communication lives in `custom_components/hyundai_jp/_api`. Home Assistant
entities and the coordinator live in its parent directory. Other regions are not
included. Keep shared helpers that Japan uses and test them alongside the Japan
adapter.

GitHub checks run offline regression tests, lint, formatting, typing and package
validation. Builds contain the integration and its API together. GitHub Actions
does not use a Bluelink account or send vehicle commands. Real vehicle testing is
done separately by the owner.

Use the repository's **Release** workflow to publish a new version. Each release
has a HACS-ready `hyundai_jp.zip`; earlier releases are retained for rollback.
See [the workflows](.github/workflows) for exact checks and release steps.

## Credits

Based on [kia_uvo](https://github.com/Hyundai-Kia-Connect/kia_uvo) and
[hyundai_kia_connect_api](https://github.com/Hyundai-Kia-Connect/hyundai_kia_connect_api),
with Japan work continued from
[kia_uvo_JP](https://github.com/phillip9933/kia_uvo_JP) and
[hyundai_kia_connect_api_JP](https://github.com/phillip9933/hyundai_kia_connect_api_JP).
The exact starting commits are recorded in [ORIGINS.json](ORIGINS.json).

Original MIT licenses are preserved in [LICENSE](LICENSE) and
[LICENSE.api.txt](LICENSE.api.txt). This is an unofficial integration and is not
affiliated with Hyundai.
