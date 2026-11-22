# Changelog

All notable changes to Comfyless are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Versions are the `version` field in `pyproject.toml`. This repository has no git tags and no GitHub Releases. The date on each version is the commit date (America/Los_Angeles) of the commit that set that version. Pull request numbers come from the commit subjects; they are pull requests on [byvfx/comfy-serverless](https://github.com/byvfx/comfy-serverless), which is where this tree is developed. This repository has no pull requests of its own.

Where a commit's message describes files that are not in this repository, that is said below. Those files live in byvfx/comfy-serverless and were not part of the mirrored tree.

## [Unreleased]

### Changed

- The README logo is centered above the title (`405032c`). That commit is also the benchmarks re-measurement (#46, also #17). Its message records a warm-SDXL target of under 7 seconds, results on L40S and H100, and the region each container ran in. `dev.py`, `bench.py`, and the spec are not in this repository. The only file changed here is `README.md`.
- README cost line no longer says a warm image takes about 5 seconds. Spec §9 in byvfx/comfy-serverless (summarized in `405032c`) records 6.3 to 6.6 seconds for warm SDXL in us-east on 2026-11-06, and 8.0 to 8.8 seconds in Asia on 2026-11-09. The 5 second figure was the old target, which that commit's message says was raised to under 7 seconds.

## [0.3.5] - 2026-11-08

`cc66490` (#44) ships this version to the Registry. The commit message names the sidebar, partner-node sign-in, live previews, and starter packs. The commit itself only updates the version strings in `pyproject.toml`, `bridge/runner.py`, and `bridge/setup.py`. The changes below are the commits after 0.3.4.

### Added

- Modal Cloud tab on ComfyUI's left sidebar (`f72cea8`, #43). Setup, Check setup, Cloud models, the Run on Cloud switch, the GPU, and Stop now are on that tab. The Extensions → Modal Cloud menu remains for frontends without the sidebar API.
- Comfy sign-in is forwarded with the job (`auth_token_comfy_org`, `api_key_comfy_org`, plus `comfy_usage_source` and `preview_method`), and the worker streams live preview frames back (`37d0d69`). A cloud run that uses partner nodes with no Comfy sign-in or API key is stopped before it is sent.
- Starter packs in Cloud models: open-weight model sets with ComfyUI's templates (`6da86ce`). The packs sit at the top of the window (`0e4ca86`). LTX-2.3 render-passes and Flux.2 Klein were added, and each pack group collapses (`f75626a`).

### Fixed

- On a fresh Windows install, the Civitai version lookup keeps the system trust store and also uses certifi's roots. The README troubleshooting covers `CERTIFICATE_VERIFY_FAILED` and the Visual C++ runtime (`81f7a2a`, #42). The Windows Sandbox script named in that commit is not in this repository.

## [0.3.4] - 2026-11-06

`3a7bf97` (#41) publishes the new logo and the GPL-3.0-only license name. That commit only bumps version strings. The change is `65425c6` (#40).

### Added

- Logo at `docs/images/icon.png` and `docs/images/icon.svg`, and an `Icon` URL in `pyproject.toml`.

### Fixed

- `license` is the SPDX name `GPL-3.0-only` (with `license-files`) instead of `license = { file = "LICENSE" }`, so the Registry stores the license name.

## [0.3.3] - 2026-11-06

`dced0b2` (#39) only bumps the version strings. The commit message does not say what shipped. The commits after 0.3.2 are below.

### Added

- Quickstart README with screenshots (`e2c2496`, #23, #33), including the install fallback, cost, how to stop paying, and the troubleshooting table.

### Changed

- The cloud worker records the final event-flush time on a successful timing result, and reports the cloud provider and region it ran in (`8b68d47`, #17, #34). Reference workflows, `bench.py`, and the written results named in that commit are not in this repository. Only `backend/app.py` changed here.

## [0.3.2] - 2026-11-06

### Added

- Stop now (`a308e8c`, #22, #32). Cancels running and queued cloud jobs and cloud downloads, then stops this app's remaining containers. The version was set to 0.3.2 in that commit.

## [0.3.1] - 2026-11-06

### Added

- When a cloud run stops because the workflow's models are only on this computer, a window lists them and can upload them (`efe20ab`, #31). The version was set to 0.3.1 in that commit.

## [0.3.0] - 2026-11-06

### Added

- Cloud models (`29f5f2c`, #30): list the models volume, download from a Hugging Face or Civitai link in the cloud, upload a local model, stop a download, or delete a cloud model. The version was set to 0.3.0 in that commit, from 0.2.0.

## [0.2.0] - 2026-11-06

Included because the history from 0.1.0 is one commit long.

### Added

- Deploy mirrors custom node packs and the local ComfyUI release to the cloud (`fad091b`, #29). The cloud uses that release when it is v0.44.0 or newer, and v0.44.0 otherwise. The version was set to 0.2.0 in that commit, from 0.1.0.

## [0.1.0] - 2026-11-05

First version in this repository. The spec and the earlier milestones (M1, M2) are in byvfx/comfy-serverless and are not in this tree.

### Added

- In-app setup (Modal sign-in, optional Hugging Face and Civitai keys, deploy, test run), Check setup and spending, and packaging as Comfyless (`08fc65c`, #27). `pyproject.toml` starts at 0.1.0 in that commit, with `modal>=1.6.3,<2`.

### Fixed

- Registry publish runs `comfy-cli==1.25.0` directly. `publish-node-action` was installing the latest comfy-cli, and 1.25.0 rejected the `--yes` flag that action passed (`8ee450b`, #28). Still version 0.1.0.
