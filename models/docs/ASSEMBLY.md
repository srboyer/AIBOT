# Printing and assembling the MAICA EVE shell

Use the magnet-pocket front, PCBFit back, and both button caps linked in the [model README](../README.md). Each part is supplied in millimeters.

## Hardware

| Item | Quantity | Specification |
|---|---:|---|
| Internal magnet | 1 | Axially magnetized N52 disc, 1 inch diameter x 3/16 inch thick (25.4 x 4.7625 mm) |
| Computer-side target | 1 | Selected totalElement adhesive steel disc, 1.25 inch diameter x 1 mm thick (31.75 x 1 mm) |
| PCB screws | 3 | M2 x 8 mm machine screws; heads no larger than 4.0 mm diameter x 1.5 mm high |
| PCB nuts | 3 | M2 hex nuts, nominal 4.0 mm across flats x 1.6 mm thick |
| Display screws and nuts | 2 of each | M2 hardware; the nominal assembly model uses M2 x 8 mm screws |

The PCB support holes are 2.2 mm clearance bores, with side-loading captive nut pockets. Screws engage the nuts rather than threading into printed plastic. The display attaches through its two existing front-shell mounting holes. The button caps need no additional hardware.

The front is designed around the supplied **6178 1.28-inch round TFT module** and the project's PCB with three mounting holes. Check the assembly STEP against any substituted components.

## Magnet fit test

Print `MAICA_EVE_Magnet_Fit_25p2_Print.stl` first, using the same PLA, layer height, perimeter settings, and scale intended for the shell. Its three retention ribs match the front's nominal 25.2 mm grip diameter. The 25p1 and 25p3 rings are tighter and looser diagnostic alternatives. Each ring is open through the back so the magnet can be pushed out after testing.

Test the real magnet with hand pressure. If the matching ring is excessively tight or loose, adjust the front's retention geometry before printing the full shell. Printing a different test ring does not change the supplied front model.

The main pocket has a 25.7 mm clearance bore, three localized crush ribs, a 5.05 mm depth, and an entry chamfer. A nominal magnet sits about 0.29 mm below the pocket mouth. The curved front leaves a minimum sampled plastic thickness of approximately 1.06 mm under the magnet, with greater separation elsewhere.

## PLA printing

Use a 0.4 mm nozzle, 0.16-0.20 mm layers, and at least three perimeters as a starting point. Print at 100% scale. The supplied STLs use these orientations:

- **Front:** mating rim down. Add supports for the curved shell and blind magnet pocket as needed. Keep support scars out of the retention ribs and clean the pocket floor.
- **Back:** three PCB support tips down. Use a brim around those contact points and supports beneath the curved shell.
- **Button caps:** socket ends down. A brim helps hold these small parts. Upper and lower caps differ in length, so keep them identified.

Remove supports and clear the nut pockets, button guides, USB opening, and mating edges before assembly.

## Assembly

1. Press the magnet into the inside of the front, below the display opening. Seat it flat against the pocket floor, with its rear surface slightly recessed below the pocket mouth.
2. Install the round TFT display behind the front opening using its two M2 screws and nuts. Check that its screen aligns with the opening.
3. Insert the three M2 nuts through the side openings in the back's PCB supports. The openings face toward the center of the back.
4. Insert each button cap from inside the back. Its wide retaining flange stays inside and its socket faces the PCB. Use the upper and lower files for their respective switches.
5. Position the PCB with its components facing the back cover. Align its three mounting holes with the support tips, then install the three M2 x 8 mm screws from the display-facing side. Tighten gently.
6. Connect and route the wiring, keeping it clear of the magnet, buttons, and mating rim. Press the rear assembly into the front.
7. Check that both buttons move and return freely, the enclosure closes fully, and the intended USB-C cable inserts completely. The modeled opening is 12.4 x 7.4 mm; a larger cable grip may need more clearance.
8. Attach the adhesive steel disc to the computer opposite the internal magnet, following the plate supplier's surface-preparation and adhesive instructions. Test the fully assembled enclosure for slipping and unintended detachment.

The back carries the PCB and captured button caps when removed.

## Verification limits

Digital checks cover the modeled PCB, display, magnet, hardware, supports, buttons, cable approach, and rear-assembly removal. The STEP exports were reimported locally for geometry checks. Successful Onshape cloud translation is a separate check and remains unverified for this revision.

Physical fit and holding strength remain untested. The supplied PCB model omits H1, connected wiring, and actual cable grips, and the switch's internal travel is not simulated. The magnet's advertised direct-to-steel pull rating does not establish the assembled shell's resistance to sliding: the curved plastic wall, target thickness, and contact friction affect the result.
