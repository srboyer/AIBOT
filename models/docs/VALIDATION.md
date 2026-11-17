# Model validation records

- `MAICA_EVE_Magnet_Validation.json` records the current front and complete assembly checks.
- `MAICA_EVE_PCBFit_Validation.json` records the unchanged back/button design and the pre-magnet baseline. Its front entries refer to a superseded front and are retained only as historical validation context, not as files to print.

The model bytes are unchanged from their validated local exports. Every STL and decompressed STEP was compared with its original validation SHA-256. Every ZIP was checked for corruption and contains exactly the named STEP.

Personal absolute paths in the copied JSON reports have been reduced to source basenames. Historical `work/` and `outputs/` references describe the original validation workspace; they are not additional download requirements. The reports' `file_sha256` fields describe original exports, including old documentation not distributed here. Use `../SHA256SUMS` for this folder's actual files and `../manifest.json` for each archived STEP's uncompressed checksum.

The assembly contains 101 named component leaves and 341 valid closed solids. The front passed 45 geometry checks. The individual back/button STEP exports are the original PCBFit exports; the complete assembly contains the slightly cleaned copies prepared for CAD compatibility. All are the same design revision. None of these digital checks establish physical print fit, magnet holding strength, button feel, or successful Onshape cloud translation.
