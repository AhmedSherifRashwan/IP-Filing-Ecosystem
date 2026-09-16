<!-- FICTIONAL example for testing; not a real invention -->

# Invention disclosure — self-purging bicycle tube valve with shape-memory seat

Inventor: J. Okafor (fictional). Date of disclosure: 2026-08-30. Status: bench prototype, three units built.

## 1. The problem

Bicycle inner tubes and tubeless set-ups are increasingly run with liquid sealant — typically a latex emulsion with ammonia and suspended particles — injected through the valve stem. Every time the tyre is inflated, a small quantity of sealant is drawn up into the valve core bore by the pressure surge and by capillary action along the poppet stem. Between rides the film dries. After ten to twenty inflation cycles the core seat is coated with cured latex and the valve either leaks slowly (the poppet no longer seats) or blocks entirely (the pump cannot open it). The standard remedy is to unscrew the core with a tool, scrape it or replace it, and re-inject sealant. Riders do this constantly, and cores are sold in multipacks for this reason.

Existing attempts to fix this either coat the poppet with a low-adhesion polymer, enlarge the bore, or fit a removable insert. None of them removes the film once it has formed; they only slow its build-up.

## 2. The concept

The valve core carries a seat ring made of a superelastic nickel–titanium (NiTi) alloy rather than a rigid brass seat or a rubber grommet. The seat ring has a thin, sinuous wall. During normal inflation the poppet lifts off the seat by its usual stroke. When the pump head applies a pressure differential above a threshold — which every floor pump reaches — the poppet travels beyond its normal stroke (we call this "over-travel"). The over-travel drives a shoulder on the poppet against the seat ring, which flexes radially outward and opens a secondary annular gap between the ring and the stem bore. Air at pump pressure then rushes through this purge gap, across the exact surfaces on which sealant accumulates, and blows the sealant film down into the tube where it belongs.

When the pump head is removed the differential collapses, the poppet returns, and the seat ring recovers its shape. Because the recovery of a superelastic ring is fast and stiff (it is not a slow elastomer creep), the recovering ring wipes across the poppet face with a snap. That wiping action shears off partially cured latex that a rubber seat would simply embed itself in. So the valve cleans itself twice per inflation: once by air flow through the purge gap, and once mechanically by the snap-back wipe.

The technical effect we observed is that the valve keeps sealing and keeps opening after far more sealant-loaded inflation cycles than a conventional core, with no tool and no rider action.

## 3. Components

1. Valve stem (10) — conventional Presta-pattern brass stem, 6 mm outer diameter, with a threaded bore to accept the core.
2. Core body (20) — turned brass or stainless steel, threaded to the stem, carrying the seat ring and guiding the poppet.
3. Seat ring (30) — the NiTi element. A short tube with a wall that is not straight in section but has one or two shallow convolutions (a wave), so that axial load on its upper edge translates into radial expansion. It is pressed into the core body with a light interference fit and located against a shoulder.
4. Poppet (40) — the moving valve member with a conical sealing face (42) that seats on the upper edge of the seat ring, and a purge shoulder (44) below the sealing face that only contacts the ring during over-travel.
5. Return spring (50) — a light compression spring under the poppet, as in a conventional core, but with a two-rate characteristic: a soft first stage for normal opening and a stiffer second stage that the pump differential must overcome to reach over-travel.
6. Purge gap (60) — the annular clearance between the seat ring outer surface and the core body bore that opens when the ring expands; closed at rest.
7. Lock nut (70) — conventional, for manual closing of the poppet for storage and transport.

## 4. Dimensions and ranges (prototype values, with the ranges we believe would work)

- Seat ring inner diameter: 2.3 mm on the prototype (range 2.0–2.6 mm).
- Seat ring wall thickness: 0.20 mm (range 0.15–0.30 mm).
- Seat ring free length: 3.0 mm (range 2.5–4.0 mm).
- Convolution amplitude: 0.12 mm (range 0.08–0.20 mm).
- Normal poppet stroke: 0.6 mm; over-travel beyond that: 0.5 mm (range 0.4–0.9 mm).
- Purge gap when fully open: 0.15 mm radial (range 0.10–0.25 mm).
- Crack pressure of the poppet (first spring stage): 1.0 bar (range 0.8–1.5 bar).
- Pressure differential to reach over-travel (second spring stage): 3.5 bar on the prototype (range 2.5–5.0 bar).
- NiTi austenite finish temperature (Af): specified below 5 °C so the ring is superelastic at all riding temperatures.

## 5. Materials

- Seat ring: superelastic NiTi, nominally 55.8 wt% Ni, from drawn tube, shape-set and electropolished (see manufacturing note).
- Core body and stem: free-machining brass CW614N; a stainless 303 variant was also machined for corrosion resistance with ammonia-bearing sealant.
- Poppet: brass with a hard-anodised aluminium alternative considered for the sealing face; the prototype is brass.
- Spring: stainless music wire. Nothing in the concept depends on the spring material.

## 6. Alternatives we have considered

Alternative A — polymer seat ring. A PEEK ring with the same convoluted wall would give some radial compliance and would be cheaper, but its recovery is slower and it has a lower snap-back force, so the wiping effect is weaker. We have not built this; it is a considered option for a lower-cost product line.

Alternative B — two-piece seat. A rigid brass seat for sealing plus a separate thin stainless spring wiper ring below it that is deflected outward by the purge shoulder. This separates the sealing function from the purge/wipe function and might be easier to manufacture, at the cost of one more part and more axial length. Not built.

A thermal variant was also discussed: a NiTi ring trained with a two-way shape memory so that warming it by hand (Af around 35–40 °C) opens the purge gap for a manual purge without a pump. We are not sure this is worth pursuing and have no data.

## 7. Manufacturing note

Seat rings were laser-cut from NiTi tube (2.7 mm OD, 0.20 mm wall) to the convoluted profile on a mandrel, shape-set at 500 °C for 10 minutes in a fixture holding the wave geometry, water-quenched, then electropolished to remove the oxide and the heat-affected edge. Rings were pressed into the core body with an interference of approximately 0.03 mm on diameter and seated against the internal shoulder. Ordinary turning for the body and poppet; no other special process.

## 8. Test result

Bench test, 2026-08-12 to 2026-08-20. Three prototype cores and three conventional cores (same manufacturer's standard Presta core) were fitted to identical 700×28 tubes each charged with 60 ml of latex sealant. Each tube was cycled: inflate to 6 bar with a floor pump, hold 24 hours at 22 °C, deflate fully through the valve, repeat. Failure was defined as either a leak-down of more than 0.5 bar in 24 hours or the pump failing to open the valve within 5 seconds of engagement.

All three conventional cores failed between cycle 12 and cycle 18. All three prototype cores completed 40 cycles without failure; the mean inflation time at cycle 40 was within 15 % of cycle 1. The full table is in `data/bench-test.md`. On disassembly at cycle 40 the prototype seat rings showed a thin cured film on the outside of the ring (in the purge gap region) but a clean sealing edge and a clean poppet face.

## 9. Known unknowns

1. Fatigue life of the seat ring. We have 40 over-travel cycles per ring. A commercial valve should survive several thousand. NiTi in superelastic cycling can accumulate residual strain; we have no data on the ring's behaviour beyond 40 cycles and do not know whether the purge gap stays closed at rest after long service.
2. CO2 inflation. A CO2 cartridge inflator chills the valve to well below 0 °C for a few seconds. If the ring's temperature drops below its martensite start temperature it will lose its superelastic recovery temporarily. We do not know whether the specified Af below 5 °C is a sufficient margin, or whether a cold-shocked ring stays deformed until it warms up.

We would also like to know whether the effect holds with ammonia-free sealants, which cure differently, but we have not tested any.
