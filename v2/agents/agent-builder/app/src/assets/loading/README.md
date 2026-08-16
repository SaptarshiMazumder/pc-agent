# Loading animations

Three Lottie animations, cycled by `components/Thinking.tsx` while a run is in flight.

## Where they came from

Downloaded as `.lottie` files (ZIP containers) from lottie.host and **unpacked** — the animation
JSON is extracted from `animations/*.json` inside each archive and committed here. They are
imported directly rather than fetched, because this window is served off the daemon and has to
work on a machine with no internet: a loader that 404s while the agent is mid-run would be the
worst possible thing to be missing.

Two of the three carried their animation under the same internal name (`animations/12345.json`),
so the container's filename is not an identifier. They are numbered here by position in the reel.

## What was changed

`loading-3.json` shipped a layer named `BG` — a plate filled `#463268` covering the whole
artboard. At 46px on a black surface that plate *was* the loader: a purple square with a small
drink inside it. The layer is removed. Nothing else is edited.

If you replace any of these, check for a background layer first. The check is a full-bleed shape,
or simply a layer called BG.

## Sizing

Each animation paints a different fraction of its own canvas — `loading-3` is a 145×315 martini in
the middle of a 500² artboard. Rendered as-is into one 58px box, one is a speck and another fills
the frame and clips.

So `Thinking.tsx` carries a hand-measured `viewBox` per animation, cropped to what each actually
paints and squared with 8% air. **Measuring this with `getBBox()` at runtime does not work** — it
returns the bounds of all geometry, including shapes that are fully transparent on that frame or
parked off-stage, and it was wrong in both directions.

To re-measure after swapping one, render it to a canvas and scan the alpha channel across its loop
(playwright + the FULL lottie build, which has the canvas renderer the light one lacks), union the
painted bounds, convert back to artboard units, then square around the painted centre.

## Constraints on a replacement

* **No raster images and no expressions.** The player is lottie-web's *light* SVG build, which
  omits the expression and effect engines. All three of these are plain shape layers.
* **Transparent background**, per the above.
* Keep them small. These three are ~300 KB of JSON in total and are inlined into the bundle.
