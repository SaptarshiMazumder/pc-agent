"""Instagram posts from collections: planned, changed, rendered.

start    a collection -> a post, planned by the agent's model from the playbook (PostPlanner),
         with any questions it needs answered (where each product was found…)
replan   the same, again, with the user's answers and wishes
update   the user's own changes, kept exactly: slides (order, words, timing, clip cuts),
         caption, hashtags, format, name — free and instant
render   the files Instagram takes, in order (a carousel's slides, or one Reel), with the caption
         and a zip — no model runs, so nothing is paid
"""

from __future__ import annotations

import time

from ad_generation.application.interfaces.brand_store import BrandStore
from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.collection_store import CollectionStore
from ad_generation.application.interfaces.design_renderer import DesignRenderer
from ad_generation.application.interfaces.media_probe import MediaProbe
from ad_generation.application.interfaces.post_store import PostStore
from ad_generation.application.interfaces.progress_reporter import ProgressReporter
from ad_generation.application.interfaces.slide_renderer import SlideRenderer
from ad_generation.application.post_page_check import PostPageCheck
from ad_generation.application.post_planner import PostPlanner
from ad_generation.domain.collection import Collection
from ad_generation.domain.instagram_format import FORMATS
from ad_generation.domain.design_progress import DesignProgress
from ad_generation.domain.post import Post, Slide

_EDITABLE = ("slides", "caption", "hashtags", "format", "name")


class PostService:
    def __init__(
        self,
        posts: PostStore,
        collections: CollectionStore,
        brands: BrandStore,
        campaigns: CampaignStore,
        renderer: SlideRenderer,
        designs: DesignRenderer,
        probe: MediaProbe,
        planner: PostPlanner | None,
        progress: ProgressReporter,
        pages: PostPageCheck | None = None,
    ) -> None:
        self._posts = posts
        self._collections = collections
        self._brands = brands
        self._campaigns = campaigns
        self._renderer = renderer
        self._designs = designs
        self._probe = probe
        self._planner = planner
        self._pages = pages
        self._progress = progress

    # ---- reading -------------------------------------------------------------------------------

    def all(self) -> list[Post]:
        return self._posts.all()

    def get(self, slug: str) -> Post:
        return self._posts.get(slug)

    def design_progress(self, slug: str) -> DesignProgress | None:
        return self._posts.design_progress(slug)

    def for_session(self, session: str) -> Post | None:
        return next((p for p in self._posts.all() if session and p.session == session), None)

    # ---- planning ------------------------------------------------------------------------------

    def start(self, collection: str, fmt: str, notes: str, session: str) -> tuple[Post, list[str]]:
        found = self._collections.find(collection)
        if found is None:
            raise KeyError(f"no collection '{collection}' (collections: {', '.join(c.name for c in self._collections.all()) or 'none yet'})")
        if not found.items:
            raise ValueError(f"collection '{found.name}' is empty — add images and clips to it from a campaign's Generations")
        if fmt not in FORMATS:
            raise ValueError(f"a post is a {' or a '.join(FORMATS)}, not '{fmt}'")
        self._progress.say(f"planning a {fmt} from {len(found.items)} items of '{found.name}'")
        answer, slides = self._planner_or_fail().plan(
            self._facts(found, fmt, notes, None), found, self._looks(found), self._brands.get().tagline
        )
        name = str(answer.get("name") or found.name).strip()
        post = Post(
            slug=self._posts.new_slug(name), name=name, collection=found.slug, format=fmt, slides=slides,
            caption=str(answer.get("caption") or "").strip(), hashtags=self._tags(answer), session=session,
        )
        post.check()
        self._posts.save(post)
        return post, [str(q) for q in answer.get("questions") or [] if str(q).strip()]

    def replan(self, slug: str, notes: str) -> tuple[Post, list[str]]:
        post = self._posts.get(slug)
        found = self._collections.get(post.collection)
        self._progress.say(f"re-planning {post.name}")
        answer, slides = self._planner_or_fail().plan(
            self._facts(found, post.format, notes, post), found, self._looks(found), self._brands.get().tagline
        )
        # A slide the new plan keeps as it was (its picture, its words) keeps its design; the rest
        # need designing again.
        before = list(post.slides)
        slides = [s.with_design_of(next((b for b in before if b.item == s.item and b.words() == s.words()), s)) for s in slides]
        post.slides, post.caption, post.hashtags = slides, str(answer.get("caption") or post.caption).strip(), self._tags(answer) or post.hashtags
        post.check()
        self._posts.save(post)
        return post, [str(q) for q in answer.get("questions") or [] if str(q).strip()]

    def update(self, slug: str, changes: dict) -> Post:
        unknown = [k for k in changes if k not in _EDITABLE]
        if unknown:
            raise ValueError(f"a post's {', '.join(unknown)} cannot be changed (only {', '.join(_EDITABLE)})")
        post = self._posts.get(slug)
        if "slides" in changes:
            kinds = {i.path: i.kind for i in self._collections.get(post.collection).items}
            slides = []
            for raw in changes["slides"]:
                if raw.get("item") not in kinds:
                    raise ValueError(f"slide item '{raw.get('item')}' is not in collection '{post.collection}'")
                slides.append(Slide.from_dict({**raw, "kind": kinds[raw["item"]]}))
            post.slides = slides
        if "caption" in changes:
            post.caption = str(changes["caption"])
        if "hashtags" in changes:
            post.hashtags = [str(h).strip().lstrip("#") for h in changes["hashtags"] if str(h).strip()]
        if "format" in changes:
            post.format = str(changes["format"])
        if "name" in changes:
            post.name = str(changes["name"]).strip() or post.name
        post.check()
        self._posts.save(post)
        return post

    # ---- rendering -----------------------------------------------------------------------------

    def render(self, slug: str) -> dict:
        post = self._posts.get(slug)
        post.check()
        brand = self._brands.get()
        out = self._posts.out_dir(slug)
        reel = post.format == "reel"
        parts = []
        for n, slide in enumerate(post.slides, 1):
            self._progress.say(f"slide {n}/{len(post.slides)}")
            parts.append(self._slide_file(slide, brand, post.format, f"{out}/{n:02d}", reel))
        files = [self._renderer.stitch(parts, f"{out}/reel.mp4")] if reel else parts
        # Every video is played through before it is handed over: one that does not decode is said,
        # with what broke, instead of reaching Instagram.
        broken = {f: errs for f in files if f.endswith(".mp4") and (errs := self._probe.decode_errors(f))}
        if broken:
            raise ValueError("rendered video(s) do not play: " + "; ".join(f"{f}: {e}" for f, e in broken.items()))
        post.design, post.canva_url = "renderer", ""
        caption = post.caption.strip() + ("\n\n" + " ".join(f"#{h}" for h in post.hashtags) if post.hashtags else "")
        caption_path, zip_path = self._posts.bundle(slug, files, caption)
        post.rendered, post.rendered_at = files, time.time()
        self._posts.save(post)
        return {"files": files, "caption": caption, "caption_file": caption_path, "zip": zip_path}

    def attach(self, slug: str, files: list[str], canva_url: str = "") -> dict:
        """A design made in Canva (the agent filled a template in the browser and downloaded its
        pages): those files become the post's, in order, with the caption and a zip — the same
        hand-over as a render. ONLY when every page is its slide: one page per slide, each showing
        that slide's photo and words (PostPageCheck). Otherwise nothing changes and the refusal
        says, page by page, what each one shows."""
        if self._pages is None:
            raise RuntimeError("checking a design's pages needs the agent's vision model")
        post = self._posts.get(slug)
        own = [f for f in files if f.startswith((f"posts/{slug}/out/", "collections/", "campaigns/", "uploads/"))]
        if own:
            raise ValueError(
                "those are the post's own pictures, not a design's downloads — attach only the files downloaded "
                f"from the design tool: {', '.join(own)}"
            )
        staged = self._posts.stage_files(slug, files)
        if self._posts.same_as_render(slug, staged):
            self._posts.discard(slug)
            raise ValueError("those pages are this post's own render, not a design's downloads — attach the design tool's files")
        if len(staged) != len(post.slides):
            self._posts.discard(slug)
            raise ValueError(
                f"the design has {len(staged)} pages and the post's plan has {len(post.slides)} slides — "
                "make one page per slide, in the plan's order"
            )
        verdicts = []
        for n, (page, slide) in enumerate(zip(staged, post.slides), 1):
            self._progress.say(f"checking page {n}/{len(staged)} against its slide")
            verdicts.append(self._pages.check(n, self._look(page, f"{slug}-page-{n:02d}"), slide, self._look(slide.item, f"{slug}-slide-{n:02d}")))
        if not all(v.matches for v in verdicts):
            self._posts.discard(slug)
            raise ValueError(
                "the design is not this post's plan, so it was not attached:\n" + "\n".join(v.line() for v in verdicts)
            )
        made = self._posts.promote(slug)
        caption = post.caption.strip() + ("\n\n" + " ".join(f"#{h}" for h in post.hashtags) if post.hashtags else "")
        caption_path, zip_path = self._posts.bundle(slug, made, caption)
        post.rendered, post.rendered_at = made, time.time()
        post.design, post.canva_url = "canva", canva_url.strip()
        self._posts.save(post)
        return {
            "files": made, "caption": caption, "caption_file": caption_path, "zip": zip_path,
            "canva_url": post.canva_url, "checked": [v.line() for v in verdicts],
        }

    def _slide_file(self, slide: Slide, brand, fmt: str, stem: str, reel: bool) -> str:
        """A designed slide from its design (a clip plays in its slot; motion when it moves or in a
        Reel); a plain slide — the photo or clip with its words over it — as before."""
        if not slide.design:
            return self._renderer.render(slide, brand, fmt, stem, as_video=reel)
        if slide.kind == "video":
            return self._designs.motion(slide.design, fmt, f"{stem}.mp4", 0, clip=slide.item, edit=slide.edit)
        if reel or self._designs.animated(slide.design):
            return self._designs.motion(slide.design, fmt, f"{stem}.mp4", slide.seconds)
        return self._designs.still(slide.design, fmt, f"{stem}.jpg")

    def _look(self, path: str, name: str) -> str:
        """A picture of a page or a slide for the checker — a clip as its middle frame."""
        if path.lower().endswith((".mp4", ".mov", ".webm")):
            return self._renderer.still_of(path, f"{self._posts.scratch('checks')}/{name}.jpg")
        return path

    # ---- pieces --------------------------------------------------------------------------------

    def _planner_or_fail(self) -> PostPlanner:
        if self._planner is None:
            raise RuntimeError("planning a post needs the agent's model")
        return self._planner

    def _looks(self, collection: Collection) -> list[str]:
        """What the planner sees: each item's picture, in order — a clip as its middle frame."""
        out = []
        for n, i in enumerate(collection.items, 1):
            out.append(i.path if i.kind == "image" else self._renderer.still_of(i.path, f"{self._posts.scratch(collection.slug)}/look-{n:02d}.jpg"))
        return out

    def _facts(self, collection: Collection, fmt: str, notes: str, current: Post | None) -> dict:
        products = {}
        for c in {i.campaign for i in collection.items if i.campaign}:
            profile, brief = self._campaigns.profile(c), self._campaigns.brief(c)
            products[c] = {
                "product": profile.name, "category": profile.category, "description": profile.description,
                "details": list(profile.must_keep), "colours": list(profile.colors), "materials": list(profile.materials),
                "label_text": list(profile.label_text), "copy": brief.copy, "caption_idea": brief.caption,
            }
        return {
            "format": fmt,
            "brand": self._brands.get().to_dict(),
            "items": [
                {"image": n, "path": i.path, "kind": i.kind, "product": i.product, "note": i.note,
                 **({"seconds": round(self._renderer.seconds(i.path), 2)} if i.kind == "video" else {})}
                for n, i in enumerate(collection.items, 1)
            ],
            "products": products,
            "sources": [
                {k: v for k, v in {"product": p.name, "found_at": p.found_at, "link": p.link, "price": p.price}.items() if v}
                for p in collection.products
            ],
            "notes": notes,
            "current_plan": current.to_dict() if current else None,
        }

    @staticmethod
    def _tags(answer: dict) -> list[str]:
        return [str(h).strip().lstrip("#") for h in answer.get("hashtags") or [] if str(h).strip()]
