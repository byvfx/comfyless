# Changelog

All notable changes to Comfyless are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions match `pyproject.toml`.

## [Unreleased]

- The sidebar tab, menus, dialogs, and settings group are labeled Comfyless. Modal still means your Modal account.
- Replaced the logo with the Unplugged mark: a warm persimmon cloud with a plug cut from the bottom, on a charcoal tile.
- Centered the logo above the title in the README.
- Clarified the README: which ComfyUI version the cloud runs, how custom node packs are installed, and that each starter pack shows its GPU.
- The test suite now lives in this repository and runs in CI on Ubuntu and Windows with Python 3.10 and 3.12.
- Documented how to install the dev dependencies and run pytest.

## [0.3.5] - 2026-11-08

- Added a Modal Cloud tab on the left sidebar for setup, cloud models, the run switch, the GPU, and Stop now.
- Partner nodes on the cloud use your Comfy account, and runs show live previews.
- Cloud models offers starter packs of open-weight models with ComfyUI's templates, including LTX-2.3 and Flux.2 Klein.
- A fresh Windows install can look up Civitai models, and the guide covers the certificate and Visual C++ errors that showed up there.

## [0.3.4] - 2026-11-06

- Added the Comfyless logo.
- The Comfy Registry shows the license as GPL-3.0.

## [0.3.3] - 2026-11-06

- Replaced the README with a quickstart, screenshots, costs, and a troubleshooting table.

## [0.3.2] - 2026-11-06

- Stop now switches off every cloud GPU at once and cancels running cloud jobs and downloads.

## [0.3.1] - 2026-11-06

- If a cloud run needs models that are only on this computer, Comfyless can upload them from that error.

## [0.3.0] - 2026-11-06

- Added Cloud models: download from a Hugging Face or Civitai link, upload a local file, or delete a model in your Modal account.

## [0.2.0] - 2026-11-06

- Deploy installs your custom node packs on the cloud and follows your ComfyUI release from v0.44.0 up.

## [0.1.0] - 2026-11-05

- Sign in to Modal, deploy, run a test, and check spending from inside ComfyUI.
