# NgpCraft Libretro 0.4

Native Libretro frontend for RetroArch, sharing the desktop C++ sources in `../cpp` and the clean-room HLE BIOS in `../hle_bios`. Python and Qt are not required. No game ROMs or proprietary BIOS images are included.

## Use the Windows x64 core

Copy `prebuilt/windows-x86_64/ngpcraft_libretro.dll` to RetroArch's cores directory and the accompanying `.info` to its info directory. Select **Load Core**, then **Load Content** and your own NGPC ROM. Other operating systems require a build from source.

RetroPad mappings: D-pad for movement, B for NGPC A, A for NGPC B, Start for Option. Native output is 160 x 152 at about 59.95 Hz, with stereo 44.1 kHz audio. Save RAM, save states and rewind are supported. An external BIOS is optional; the clean-room HLE is embedded.

**RetroAchievements** — the core exposes `RETRO_MEMORY_SYSTEM_RAM` as the 16 KiB at `0x4000..0x7FFF` (work RAM + shared Z80 RAM), the layout Beetle NGP uses, so existing NGP achievement sets read the same bytes. RetroArch's memory-based cheat search can also read and write it.

**Cheats** — `retro_cheat_set` takes the desktop emulator's format, `ADDR[:SIZE] = VALUE` in hex (size 1, 2 or 4, default 1, value little-endian), several codes joined by `+`, e.g. `4812:1 = 63+481A:2 = 03E7`. Enabled codes are written once per frame. A code aimed outside RAM (`0x4000..0xBFFF`) or that does not parse is refused whole and logged — a write into the cartridge would be a command to its save flash.

## Build from the repository root

```sh
cmake -S libretro -B libretro/build -DCMAKE_BUILD_TYPE=Release
cmake --build libretro/build --config Release
ctest --test-dir libretro/build -C Release --output-on-failure
```

For cartridge tests, configure with `-DNGPCRAFT_TEST_ROM=/path/to/game.ngp` and optionally `-DNGPCRAFT_TEST_ROM_LINK=/path/to/link_probe.ngc`. Without supplied ROMs, cartridge tests are not registered.

Version 0.4 synchronizes the flash timing model and HLE BIOS with the desktop. The update passed five Libretro tests and 49 flash/save tests. The prebuilt DLL is Windows x64 only.
