Design ONE slide of an Instagram post as a single HTML page with CSS, following the design playbook
below. It is rendered by a headless browser at exactly the canvas size and posted as is.

FACTS holds: the canvas (width × height), the format (carousel or reel), the slide's purpose, the
WORDS it must carry (exactly), the pictures and clips you may use (workspace paths, kind, size),
which one is this slide's main item, the brand's look (fonts, colours), the brand's DESIGN NOTES
(rules the user has taught — every one is a must), the user's BRIEF (their wishes for this slide, in
their words), and —

WHAT WINS, in order: the BRIEF, then the DESIGN NOTES, then the reference, then the playbook below.
What the brief removes or changes stays removed or changed — never brought back to match a
reference. The WORDS are always FACTS `words`, exactly; a brief cannot add or change words.
when given — a TEMPLATE to start from (adapt it: replace ASSET_n / VIDEO_1 with real paths and the
{{…}} placeholders with the real words; change anything that does not suit these pictures and
words). When `previous` is given, it is your last design and `problems` what was wrong with it:
fix every problem.

When FACTS has a `reference`, the LAST attached picture is that reference design and `reference.spec`
is what a designer read off it. Take from it its LOOK — the type classes and hierarchy, the palette
roles, the kind of decoration, where the text sits, the mood — so the slide looks as finished and as
designed as it does. Its big shapes are NOT copied at their size: the user's picture is the hero.
Rebuild it with THIS slide's pictures and words: never copy its words, brand names or logos, never
put its picture in the design, and fit the structure to these words (fewer text slots when there are
fewer words; drop a photo slot rather than repeat a picture). Fonts: pick the Google Fonts closest
to its type classes. Shapes, frames, badges, sparkles and colour blocks are drawn in CSS or inline
SVG — not loaded.

The attached pictures are the slide's media, in the order FACTS lists them (then the reference,
when there is one) — LOOK at them: where the face and the product sit, where the photo is calm
enough for text, its colours.

The picture comes first — always:
- The slide's main picture or clip fills the WHOLE canvas (full-bleed), unless the brief asks for
  something else. Never shrink it into a box, a circle or a collage cell.
- Everything drawn over it (panels, shapes, colour blocks, solid scrims) covers at most a quarter of
  the canvas, and never a face or the product (the piece the slide is about — find it in the
  picture). Text sits over a soft gradient scrim or a calm, plain part of the picture.
- ONLY the words in FACTS `words`, each exactly once — never the brand's name, handle or tagline,
  never a label, a date, a hashtag or any word that is not in `words`.

Technical rules — a design that breaks one is refused:
- One complete HTML document. `html, body` exactly the canvas size, `margin: 0; overflow: hidden`.
- Fonts only through one Google Fonts <link> (the families the playbook names). No other URLs.
- Pictures and clips only by the workspace paths given in FACTS, used as written (<img src="...">
  with object-fit: cover, or CSS background-image). Nothing else is loaded.
- No <script>, no JavaScript, no forms, no iframes. Motion only with CSS @keyframes.
- Inline <svg> is welcome for shapes and ornaments the design calls for (no <image> or links in it).
- A slide whose main item is a CLIP: put exactly ONE element with `data-video="<that clip's path>"`
  (sized and placed where the clip plays — usually the whole canvas) and mark every element that
  must appear OVER the playing clip (scrims, words, shapes on top) with the attribute `data-over`.
  Everything without `data-over` is drawn under the clip.
- Use absolute positioning or flex/grid freely; keep every word inside the canvas and its safe zones.

Answer with ONE JSON object and nothing else:

{
  "html": "<!doctype html><html>…</html>",
  "notes": "one line on the design choice"
}
