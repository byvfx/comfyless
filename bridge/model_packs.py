"""Starter packs: curated sets of open-weight models that run on the user's own Modal GPU, each
paired with the ComfyUI templates that use them (Workflow > Browse Templates).

A pack is just data: the files it needs in the models volume (folder/name, download link, size) and
the template names to open. The Cloud models window shows which files are already in the cloud,
downloads the missing ones with the regular model downloader, and opens a template with the exact
file names the pack puts in the volume, so the cloud dropdowns match without any editing.

Links and names come from the templates themselves (each template's node properties list the
models it needs), so a pack opens cleanly in the ComfyUI version the templates ship with.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass

log = logging.getLogger("modal-bridge")

# folder/name -> (download link, size in bytes). Sizes are what Hugging Face reported when the pack
# was written; they only size the download for the user.
FILES: dict[str, tuple[str, int]] = {
    "background_removal/birefnet.safetensors": ("https://huggingface.co/Comfy-Org/BiRefNet/resolve/main/background_removal/birefnet.safetensors", 444473596),
    "checkpoints/sam3.1_multiplex_fp16.safetensors": ("https://huggingface.co/Comfy-Org/sam3.1/resolve/main/checkpoints/sam3.1_multiplex_fp16.safetensors", 1745546848),
    "checkpoints/sd_xl_base_1.0.safetensors": ("https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors", 6938078334),
    "diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors": ("https://huggingface.co/Comfy-Org/flux1-kontext-dev_ComfyUI/resolve/main/split_files/diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors", 11904640136),
    "diffusion_models/qwen_image_2.1_int8_convrot.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/diffusion_models/qwen_image_2.1_int8_convrot.safetensors", 7256783064),
    "diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors", 20430679144),
    "diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors", 20430698424),
    "diffusion_models/qwen_image_edit_2509_int8_convrot.safetensors": ("https://huggingface.co/Comfy-Org/marigold-v2-0/resolve/main/diffusion_models/qwen_image_edit_2509_int8_convrot.safetensors", 20499083752),
    "diffusion_models/qwen_image_fp8_e4m3fn.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_fp8_e4m3fn.safetensors", 20430635136),
    "diffusion_models/seedvr2_3b_int8_convrot.safetensors": ("https://huggingface.co/Comfy-Org/SeedVR2/resolve/main/diffusion_models/seedvr2_3b_int8_convrot.safetensors", 3458259704),
    "diffusion_models/wan2.2_fun_control_5B_bf16.safetensors": ("https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_fun_control_5B_bf16.safetensors", 10003303280),
    "diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors": ("https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", 14294742832),
    "diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors": ("https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", 14294742832),
    "diffusion_models/wan2.2_ti2v_5B_fp16.safetensors": ("https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", 9999658848),
    "embeddings/marigold_v2_depth_conditioning.safetensors": ("https://huggingface.co/Comfy-Org/marigold-v2-0/resolve/main/embeddings/marigold_v2_depth_conditioning.safetensors", 2422904),
    "embeddings/marigold_v2_normals_conditioning.safetensors": ("https://huggingface.co/Comfy-Org/marigold-v2-0/resolve/main/embeddings/marigold_v2_normals_conditioning.safetensors", 2537592),
    "frame_interpolation/film_net_fp16.safetensors": ("https://huggingface.co/Comfy-Org/frame_interpolation/resolve/main/frame_interpolation/film_net_fp16.safetensors", 68882302),
    "geometry_estimation/depth_anything_3_mono_large.safetensors": ("https://huggingface.co/Comfy-Org/Depth-Anything-3/resolve/main/geometry_estimation/depth_anything_3_mono_large.safetensors", 1336748056),
    "loras/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors": ("https://huggingface.co/lightx2v/Qwen-Image-2512-Lightning/resolve/main/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors", 1698951104),
    "loras/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors": ("https://huggingface.co/lightx2v/Qwen-Image-Lightning/resolve/main/Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors", 849608296),
    "loras/Qwen-Image-Edit-2509-Relight.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/loras/Qwen-Image-Edit-2509-Relight.safetensors", 236117032),
    "loras/Qwen-Image-Lightning-4steps-V1.0.safetensors": ("https://huggingface.co/lightx2v/Qwen-Image-Lightning/resolve/main/Qwen-Image-Lightning-4steps-V1.0.safetensors", 1698951104),
    "loras/marigold_v2_depth_log_stage2.safetensors": ("https://huggingface.co/Comfy-Org/marigold-v2-0/resolve/main/loras/marigold_v2_depth_log_stage2.safetensors", 1703764632),
    "loras/marigold_v2_normals.safetensors": ("https://huggingface.co/Comfy-Org/marigold-v2-0/resolve/main/loras/marigold_v2_normals.safetensors", 1703764632),
    "loras/qwen_image_union_diffsynth_lora.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image-DiffSynth-ControlNets/resolve/main/split_files/loras/qwen_image_union_diffsynth_lora.safetensors", 943906720),
    "loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors": ("https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", 1226977424),
    "loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors": ("https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", 1226977424),
    "text_encoders/clip_l.safetensors": ("https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors", 246144152),
    "text_encoders/qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors", 9471072252),
    "text_encoders/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors", 9471072252),
    "text_encoders/qwen3vl_8b_int8_convrot.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/text_encoders/qwen3vl_8b_int8_convrot.safetensors", 9350798360),
    "text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors", 9384670680),
    "text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors": ("https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn_scaled.safetensors", 5157348688),
    "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors": ("https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", 6735906897),
    "vae/ae.safetensors": ("https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/resolve/main/split_files/vae/ae.safetensors", 335304388),
    "vae/marigold_v2_depth_log_stage2_vae.safetensors": ("https://huggingface.co/Comfy-Org/marigold-v2-0/resolve/main/vae/marigold_v2_depth_log_stage2_vae.safetensors", 253806278),
    "vae/marigold_v2_normals_vae.safetensors": ("https://huggingface.co/Comfy-Org/marigold-v2-0/resolve/main/vae/marigold_v2_normals_vae.safetensors", 253806278),
    "vae/qwen_image_2.1_vae_bf16.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/vae/qwen_image_2.1_vae_bf16.safetensors", 675509688),
    "vae/qwen_image_vae.safetensors": ("https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors", 253806246),
    "vae/seedvr2_ema_vae_fp16.safetensors": ("https://huggingface.co/Comfy-Org/SeedVR2/resolve/main/vae/seedvr2_ema_vae_fp16.safetensors", 501324814),
    "vae/wan2.2_vae.safetensors": ("https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors", 1409400960),
    "vae/wan_2.1_vae.safetensors": ("https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors", 253815318),
}

_QWEN_IMAGE_SHARED = ("text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors", "vae/qwen_image_vae.safetensors")


@dataclass(frozen=True)
class Pack:
    id: str
    title: str
    blurb: str
    files: tuple[str, ...]
    templates: tuple[tuple[str, str], ...]  # (template name, label)
    gpu: str = "L40S"  # the smallest GPU the pack is comfortable on
    group: str = "Images"


PACKS: tuple[Pack, ...] = (
    Pack(
        "qwen-image-2512", "Qwen Image 2512",
        "Text to image with strong realism and text rendering. 4-step Lightning LoRA, so a 1328² image takes seconds.",
        ("diffusion_models/qwen_image_2512_fp8_e4m3fn.safetensors", *_QWEN_IMAGE_SHARED, "loras/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors"),
        (("image_qwen_Image_2512", "Text to image"),),
    ),
    Pack(
        "qwen-image-2.1", "Qwen Image 2.1",
        "One model for text to image, instruction edits and background removal with real transparency, at native 2K. Includes its optional prompt enhancers.",
        ("diffusion_models/qwen_image_2.1_int8_convrot.safetensors", "text_encoders/qwen3vl_8b_int8_convrot.safetensors", "vae/qwen_image_2.1_vae_bf16.safetensors",
         "text_encoders/qwen3.5_9b_qwen_image_2.1_pe_t2i.int8_convrot.safetensors", "text_encoders/qwen3.5_9b_qwen_image_2.1_pe_i2i.int8_convrot.safetensors"),
        (("image_qwen_image_2_1_t2i", "Text to image"), ("image_qwen_image_2_1_image_edit", "Image edit"),
         ("image_qwen_image_2_1_background_removal", "Remove background")),
    ),
    Pack(
        "qwen-image-edit-2509", "Qwen Image Edit 2509 + Relight",
        "Edit one or more images by instruction, and relight a plate or render with the Relight LoRA.",
        ("diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors", *_QWEN_IMAGE_SHARED,
         "loras/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors", "loras/Qwen-Image-Edit-2509-Relight.safetensors"),
        (("image_qwen_image_edit_2509", "Image edit"), ("image_qwen_image_edit_2509_relight", "Relight")),
    ),
    Pack(
        "qwen-image-union-control", "Qwen-Image Union Control",
        "Drive a generation from a depth, normal, canny, lineart or pose pass, such as a render pass from your 3D scene.",
        ("diffusion_models/qwen_image_fp8_e4m3fn.safetensors", *_QWEN_IMAGE_SHARED,
         "loras/qwen_image_union_diffsynth_lora.safetensors", "loras/Qwen-Image-Lightning-4steps-V1.0.safetensors"),
        (("image_qwen_image_union_control_lora", "Control to image"),),
    ),
    Pack(
        "flux-kontext-dev", "Flux Kontext dev",
        "Instruction-based image editing that keeps characters and style consistent. Lighter than Qwen Image Edit.",
        ("diffusion_models/flux1-dev-kontext_fp8_scaled.safetensors", "text_encoders/clip_l.safetensors",
         "text_encoders/t5xxl_fp8_e4m3fn_scaled.safetensors", "vae/ae.safetensors"),
        (("flux_kontext_dev_basic", "Image edit"),),
    ),
    Pack(
        "sdxl", "SDXL 1.0",
        "The classic fast base model. Small, cheap and the one most ControlNets and LoRAs were made for.",
        ("checkpoints/sd_xl_base_1.0.safetensors",),
        (("image_sdxl_simple", "Text to image"),),
    ),
    Pack(
        "marigold-v2", "Depth and normal passes (Marigold V2)",
        "Estimate a depth map and a surface-normal map from any image, for projection, relighting and comp.",
        ("diffusion_models/qwen_image_edit_2509_int8_convrot.safetensors",
         "loras/marigold_v2_depth_log_stage2.safetensors", "embeddings/marigold_v2_depth_conditioning.safetensors", "vae/marigold_v2_depth_log_stage2_vae.safetensors",
         "loras/marigold_v2_normals.safetensors", "embeddings/marigold_v2_normals_conditioning.safetensors", "vae/marigold_v2_normals_vae.safetensors"),
        (("image_marigold_v2_depth_estimation", "Depth"), ("image_marigold_v2_surface_normal_estimation", "Normals")),
        group="CG tools",
    ),
    Pack(
        "depth-anything-3", "Depth Anything 3",
        "Fast monocular depth maps from a single image. Small download, runs in a second.",
        ("geometry_estimation/depth_anything_3_mono_large.safetensors",),
        (("utility_depth_anything3_image_depth_estimation", "Depth"),),
        group="CG tools",
    ),
    Pack(
        "mattes", "Mattes: BiRefNet + SAM3",
        "Pull a clean matte with BiRefNet, or segment anything you can name with SAM3 and get a mask plus an RGBA cutout.",
        ("background_removal/birefnet.safetensors", "checkpoints/sam3.1_multiplex_fp16.safetensors"),
        (("utility_birefnet_remove_background", "Remove background"), ("utility_image_segment_sam3", "Segment (SAM3)")),
        group="CG tools",
    ),
    Pack(
        "upscale-seedvr2", "Upscale: SeedVR2 3B",
        "One-step diffusion upscaler that adds believable detail to renders and plates.",
        ("diffusion_models/seedvr2_3b_int8_convrot.safetensors", "vae/seedvr2_ema_vae_fp16.safetensors"),
        (("utility_seedvr2_3b_int8_upscale_image", "Upscale image"),),
        group="CG tools",
    ),
    Pack(
        "frame-interpolation", "Frame interpolation (FILM)",
        "Retime a clip to a higher frame rate, or smooth a stepped render.",
        ("frame_interpolation/film_net_fp16.safetensors",),
        (("utility_video_frame_interpolation", "Interpolate video"),),
        group="CG tools",
    ),
    Pack(
        "wan2.2-5b", "Wan 2.2 5B video",
        "Text or image to video, plus Fun Control: drive a clip from a depth, pose or edge sequence. Fits an L40S.",
        ("diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", "diffusion_models/wan2.2_fun_control_5B_bf16.safetensors",
         "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "vae/wan2.2_vae.safetensors"),
        (("video_wan2_2_5B_ti2v", "Text/image to video"), ("video_wan2_2_5B_fun_control", "Control video")),
        group="Video",
    ),
    Pack(
        "wan2.2-14b-i2v", "Wan 2.2 14B image to video",
        "The higher-quality image to video model, with 4-step LoRAs. Two 14B models, so pick the A100-80GB.",
        ("diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
         "loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", "loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
         "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "vae/wan_2.1_vae.safetensors"),
        (("video_wan2_2_14B_i2v", "Image to video"),),
        gpu="A100-80GB",
        group="Video",
    ),
)

for _pack in PACKS:  # a typo here would only show up when a user clicks the pack
    for _path in _pack.files:
        assert _path in FILES, f"pack {_pack.id}: unknown file {_path}"


def get(pack_id: str) -> Pack | None:
    return next((p for p in PACKS if p.id == pack_id), None)


@functools.lru_cache(maxsize=None)
def template_available(name: str) -> bool:
    """Whether this ComfyUI's bundled templates include the template (older installs may lack a new one)."""
    try:
        from comfyui_workflow_templates import get_asset_path

        return bool(get_asset_path(name, f"{name}.json"))
    except Exception:
        return False


def listing(cloud: dict[str, int]) -> list[dict]:
    """The packs with each file's cloud status. `cloud` maps folder/name to size for files in the volume."""
    out = []
    for pack in PACKS:
        files = []
        for path in pack.files:
            url, size = FILES[path]
            folder, _, name = path.partition("/")
            files.append({"path": path, "folder": folder, "name": name, "url": url, "size": size, "have": path in cloud})
        out.append({
            "id": pack.id, "title": pack.title, "blurb": pack.blurb, "gpu": pack.gpu, "group": pack.group,
            "files": files,
            "size": sum(f["size"] for f in files),
            "missing_size": sum(f["size"] for f in files if not f["have"]),
            "templates": [{"name": n, "label": label, "available": template_available(n)} for n, label in pack.templates],
        })
    return out
