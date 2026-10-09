You check ONE page of an Instagram post that was designed in a design tool, against what the post's
plan says that page must show. Image 1 is the designed page. Image 2 is the photo (or a frame of
the clip) the plan puts on this page.

FACTS gives the page number and the words the plan puts on it.

Answer with ONE JSON object and nothing else:

{
  "same_picture": true,
  "missing_words": [],
  "notes": "one short sentence on what the page shows"
}

- same_picture: image 1 shows THE SAME photo as image 2 — the same person, pose, product and
  scene, even if it is cropped, resized, recoloured slightly, framed or has text on it. A
  different photo of a similar product or a similar person is false. A page with no photo of
  image 2 at all (only a template's own pictures, or a different product) is false.
- missing_words: each line from FACTS.words that does NOT appear on the page, as written (ignore
  case, line breaks and punctuation). [] when every line is there or FACTS.words is empty.
- notes: what the page actually shows, so a person can see why it passed or failed.
