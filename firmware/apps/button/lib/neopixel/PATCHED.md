# neopixel 1.0.4, patched for all RTL872x platforms

A copy of the Particle `neopixel` library 1.0.4 (LGPL-3.0, see `LICENSE`), changed only so that
its RTL872x code (WS2812B over SPI) is used on every RTL872x platform, not just the P2:
each `PLATFORM_ID == 32` became `HAL_PLATFORM_RTL872X` (and `!= 32` became `!HAL_PLATFORM_RTL872X`).
That adds the M-SoM (platform 35). Nothing else differs from the published 1.0.4.

Drop this folder and use `dependencies.neopixel=<version>` in `project.properties` once a
published version supports the M-SoM.
