---
name: 0112-write-a-separate-sd-router-py-for-stable-diffusion
type: roadmap-item
status: active
title: R-0112 · Write a separate sd-router.py for stable-diffusion.cpp instead of growing the sdcpp kind
description: Closed roadmap item R-0112 (done 2026-10-30).
id: R-0112
state: done
horizon: later
origin: user:2026-10-28:sd-router
blocked_by: []
tags: [images, llm-router, sd-cpp]
closed: 2026-10-30
commit: f43a0527d04536cd3fdcfffcefa1a69430f4e4fb
---

# R-0112 · Write a separate sd-router.py for stable-diffusion.cpp instead of growing the sdcpp kind

## Why

User decision 2026-10-28: the sd integration inside llm-router stops roughly where it is, enough to call the sd-server; proper sd.cpp handling becomes its own file, sd-router.py, later. The router never generates masks and never decodes images (mask generation is a client or MCP-tool concern). Input measured live on the sd-server (flux-2-klein-9b-Q8_0, 2026-10-28, probe scripts in untracked .claude/tmp/sdmask/): /v1/images/edits with a mask but no init_image DAMAGES the upload (outside-mask error 48.6 of 255, gradient and shapes lost) because uploads go to ref_images; the working inpaint routes are /sdapi/v1/img2img (init_images, mask, denoising_strength 1.0), /sdcpp/v1/img_gen (async job, init_image, mask_image, strength) and /v1/images/edits with the init_image passed inside the prompt-embedded sd_cpp_extra_args block (all about 3-4 of 255 outside the mask; the residue is the VAE round trip, so pixel-exact preservation needs client-side compositing). White mask means regenerate. No native outpainting: the client pads image and mask. Open points for the design: which native routes to front, the sd_cpp_extra_args guards (the raw LoRA path, the LAN exposure of the unauthenticated sd-server), the interplay with the sizes and default_size route keys, and whether the sdcpp kind in llm-router is then removed or kept as a thin passthrough.

## Log

- 2026-10-28 new->later: raised by user 2026-10-28
- 2026-10-28 later->later [idea->planned]: planned in requirements.yaml
- 2026-10-30 later->done: commit f43a0527d04536cd3fdcfffcefa1a69430f4e4fb
