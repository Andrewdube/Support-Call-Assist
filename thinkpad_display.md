---
title: ThinkPad - Display flicker, lines, or no image on internal panel
models: ThinkPad T14, T16, X1 Carbon, P16, P1
symptoms: flicker, lines on screen, dim, no display, black screen external works, backlight
parts: LCD panel (FRU from parts lookup by MT and panel type); eDP display cable
---
SAMPLE ARTICLE - replace with your organization's approved content.

1. Connect an external monitor. If the external display is fine, the GPU is likely OK - focus on panel/cable.
2. Enter BIOS setup (F1 at boot). If lines/flicker also appear in BIOS, it is hardware (panel or cable), not a driver.
3. If only in Windows: update/rollback the graphics driver via Lenovo System Update; disable panel self refresh in Intel Graphics Command Center.
4. Flex the lid slowly; flicker that changes with hinge angle indicates the eDP cable.
5. Hardware: dispatch LCD panel first if the image is distorted at all angles; eDP cable if hinge-angle dependent. Check panel type (touch / non-touch / privacy) on the serial's parts list before ordering.
