# Comfyless

**Run your ComfyUI workflows on a cloud GPU, from the ComfyUI you already use.**

Comfyless adds a **Local / Cloud** switch next to ComfyUI's Run button. With Cloud on, your workflow runs on a fast GPU (L40S, A100 or H100) in your own [Modal](https://modal.com) account, and the results appear in ComfyUI as if they ran on your computer. No server to manage, no terminal, and nothing runs (or costs) while you're not generating.

> This repository is a mirror of the `comfyless/` folder in [byvfx/comfy-serverless](https://github.com/byvfx/comfy-serverless), where development, the design spec and issues live. Please open issues and pull requests there.

## What it costs

You pay Modal directly, by the second, only while a cloud GPU is on. Measured with SDXL (1024×1024, 30 steps):

- **L40S (default):** about $1.95 an hour while on. A warm image takes about 5 seconds, roughly a cent.
- **Starting a GPU** after a break takes 30 to 45 seconds.
- **After a run, the GPU stays on** for a few minutes (5 by default, adjustable) so your next run starts instantly. That idle time is billed too and is usually most of the cost of a single image: one image on its own costs about 20 cents with the 5-minute default, or about 7 cents with 1 minute. Ten images in a row cost about a cent each.

Modal's free plan includes monthly credit. Set a spending limit in the Modal dashboard ([how](https://modal.com/docs/guide/budgets)). *Extensions → Modal Cloud → Check setup and spending* shows what Modal billed you today and this month.

## Install

1. In ComfyUI, open **Manager → Custom Nodes Manager**, search for **Comfyless**, click **Install**, and restart ComfyUI.
2. The **Modal Cloud setup** window opens:
   1. **Connect your Modal account.** Approve the sign-in in your browser. No account yet? Sign up free on modal.com.
   2. **Download keys (optional).** Only for gated Hugging Face models or Civitai downloads.
   3. **Install the cloud worker.** One click; the first time takes 3 to 6 minutes.
   4. **Run a test.** Confirms everything works and shows what it cost.
3. Click **Local** next to the Run button to switch to **Cloud**, and run a workflow.

## Using it

- **Settings → Modal Cloud:** which GPU to use, and how long it stays on after a run.
- **Models:** models in your cloud appear in ComfyUI's model lists. If a workflow uses a model that's only on your computer, you get a clear message before anything is sent.
- **Input images** are uploaded automatically.
- **Cancel** works as usual.
- **Extensions → Modal Cloud:** setup, check setup and spending, refresh the cloud model list.
- **After updating Comfyless**, open Setup and click **Deploy again**.

Limitations for now: only ComfyUI's built-in nodes run in the cloud (custom node packs are coming), and cloud jobs don't appear in the queue panel.

## Privacy and security

- Your workflows, images and models stay in your own Modal account.
- Your Modal sign-in is stored where Modal's own tools keep it (`~/.modal.toml`), and download keys only in your Modal account.
- Setup can only be changed from the computer running ComfyUI.

## License

GPL-3.0, the same as ComfyUI. See [LICENSE](LICENSE).
