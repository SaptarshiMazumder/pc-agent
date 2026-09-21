"""AppAssetResponseBuilder — the bytes and content headers for one file of an app agent's UI.

WHY THIS EXISTS AS ITS OWN THING. `_serve_app` in gateway.py does routing, authorization, path
traversal guards and session cookies; deciding how a file travels over the wire is none of those.
It was three lines there (read, guess the type, set a length) and those three lines were shipping
the hosted app at 476 KB of uncompressed JavaScript per page load.

WHAT WAS ACTUALLY MEASURED, on production, 2026-09-22:

    /assets/index-*.js    475,967 bytes on the wire, 9.7 s
    /assets/index-*.css    87,095 bytes on the wire, 8.5 s
    same machine, to a CDN edge:  284 KB/s

Vite reports those two as 145 KB and 15 KB gzipped. Nothing in the path compresses them: the
daemon never did, and an Application Load Balancer does not compress at all — only CloudFront
does, and there is none in front of the daemon. So the browser downloads 3-6x the bytes it needs
to, over a link measured at 49 KB/s to this origin.

WHAT THIS DOES NOT DO. It does not change caching: every response still says `no-store`, exactly
as before. That is deliberate and it is not an oversight. Caching a content-hashed asset for a
year is the larger win on a SECOND visit, and it is also the one mistake here that a redeploy
cannot undo — mark a stable filename immutable and every browser that saw it holds it for a
year. Bytes first, because being wrong about compression costs nothing.

FONTS ARE ALREADY COMPRESSED and are the reason the numbers below stop where they do. woff2 is
Brotli inside a wrapper; gzipping it burns CPU to make it very slightly larger. The 12 font files
this app loads are ~165 KB and stay ~165 KB.
"""

from __future__ import annotations

import gzip
from hashlib import blake2b
from pathlib import Path


# MIME types worth compressing. Everything absent from this set is either already compressed
# (woff2, png, webp, jpeg) or too small for the CPU to be worth it.
#
# Matched on the type as `guess_mime` returns it, plus a `text/` prefix rule — there is no useful
# `text/*` that gzip does not shrink, and listing them individually only invites the omission.
_COMPRESSIBLE_TYPES = frozenset({
    "application/javascript",
    "application/json",
    "application/manifest+json",
    "application/xml",
    "image/svg+xml",
    "text/javascript",
})

# BELOW THIS, DON'T BOTHER. A gzip member carries ~20 bytes of header and trailer, so a small
# file can come out LARGER, and either way the round trip dominates what the body costs. 1 KiB is
# the conventional floor and nothing here argues for a different one.
_MIN_COMPRESS_BYTES = 1024

# The compressed-bytes memo. Bounded because this is a long-lived process serving a directory
# whose size it does not control: an agent with a hundred chunks must not turn into a hundred
# cached copies of itself. Oldest-first eviction, which for an asset directory is close enough to
# least-used — the entry page and its two big chunks are re-requested constantly and stay hot.
_MEMO_MAX_ENTRIES = 64

# Past this, serve it compressed but do not HOLD it compressed. A single oversized asset would
# otherwise be able to claim most of the memo's memory on its own.
_MEMO_MAX_BYTES = 4 * 1024 * 1024


class AppAssetResponseBuilder:
    """Turns a resolved file into (body, content headers).

    STATELESS EXCEPT FOR THE MEMO, which is why it takes its collaborators as arguments rather
    than holding a registry or a path root: one instance serves every agent on the daemon, and
    the memo is keyed by content, so two agents shipping byte-identical vendored SDKs share the
    compressed copy rather than each paying for their own.
    """

    def __init__(self, sdk_asset_bytes, guess_mime, vendored_sdk_rel: str) -> None:
        """
        :param sdk_asset_bytes: reads the engine's canonical SDK build, or None if none is staged.
        :param guess_mime: path -> MIME string.
        :param vendored_sdk_rel: the SDK's path inside an agent's ui/, as the agent ships it.

        INJECTED, NOT IMPORTED, so this file does not reach back into the gateway's module graph
        for the three things it needs. The gateway already owns all three.
        """
        self._sdk_asset_bytes = sdk_asset_bytes
        self._guess_mime = guess_mime
        self._vendored_sdk_rel = vendored_sdk_rel
        self._memo: dict[bytes, bytes] = {}

    # ── the public call ───────────────────────────────────────────────────────

    def build(self, target: Path, ui_root: Path, accept_encoding: str) -> tuple[bytes, dict]:
        """The body to send and the headers describing it.

        The caller adds everything about the REQUEST — the session cookie, the status line. This
        answers only "what is this file and how does it travel".
        """
        body = self.body_for(target, ui_root)
        mime = self._guess_mime(target)

        headers = {
            "Content-Type": mime,
            # UNCHANGED FROM BEFORE THIS CLASS EXISTED: local-first, always the installed
            # version. An agent rebuilt on a desktop must be visible on the next reload, and the
            # entry page must never be a version behind the daemon serving it.
            "Cache-Control": "no-store",
        }

        if not self._should_compress(mime, len(body), accept_encoding):
            headers["Content-Length"] = str(len(body))
            return body, headers

        packed = self._gzipped(body)
        headers["Content-Encoding"] = "gzip"
        # VARY, or a shared cache in front of this serves the gzipped body to a client that did
        # not ask for one. There is no such cache today; there will be the moment CloudFront goes
        # in, and a header that only matters later is still wrong to omit now.
        headers["Vary"] = "Accept-Encoding"
        headers["Content-Length"] = str(len(packed))
        return packed, headers

    # ── the bytes ─────────────────────────────────────────────────────────────

    def body_for(self, target: Path, ui_root: Path) -> bytes:
        """The bytes to serve for one app asset — with the vendored SDK substituted for the copy
        on disk.

        WHY THE FILE ON DISK IS NOT TRUSTED. `vendor/agentd-client.js` is COPIED into an agent
        when it is scaffolded, so it is a snapshot of whatever the SDK was on the day that agent
        was born and it never changes again. Pack time re-vendors it (bundle_io.pack_bundle),
        which covers agents that arrive as a package — but an agent AUTHORED here is served
        straight off disk and is never packed at all. Those copies age silently against a daemon
        that keeps moving.

        That is not a theoretical decay. When the accounts service stopped returning the
        pre-token `login.token` field, every agent holding an older SDK read a field the server
        no longer sent and reported "the accounts server returned no session token" — while the
        server answered 200. Rebuilding the image fixed the agents shipped IN it and none of the
        ones users had already created, because those live in each account's own directory.

        Substituting here fixes all of them at once, with no migration to run and nothing to
        remember: the SDK an app loads is the SDK belonging to the engine serving it, always.

        FAILS OPEN. An install with no canonical SDK staged (`_data/sdk/`, see runtime_paths)
        serves the file on disk exactly as before — a missing build asset must never turn into a
        404 on the one script the page cannot start without.
        """
        if target.name != Path(self._vendored_sdk_rel).name:
            return target.read_bytes()  # the overwhelmingly common path: not the SDK, no work
        try:
            if target.relative_to(ui_root).as_posix() != self._vendored_sdk_rel:
                return target.read_bytes()
            canonical = self._sdk_asset_bytes()
            return canonical if canonical is not None else target.read_bytes()
        except (ValueError, OSError):
            return target.read_bytes()

    # ── compression ───────────────────────────────────────────────────────────

    def _should_compress(self, mime: str, size: int, accept_encoding: str) -> bool:
        if size < _MIN_COMPRESS_BYTES:
            return False
        base = mime.split(";", 1)[0].strip().lower()
        if not (base.startswith("text/") or base in _COMPRESSIBLE_TYPES):
            return False
        return self._accepts_gzip(accept_encoding)

    @staticmethod
    def _accepts_gzip(accept_encoding: str) -> bool:
        """Does this client want gzip?

        `q=0` IS A REFUSAL, not a preference, and it is the whole reason this is not an `in`
        check: "gzip;q=0" contains the substring "gzip" and means the exact opposite of it. Rare
        from browsers, routine from probes and proxies, and sending a body someone explicitly
        refused is not a thing to get wrong for one saved line.
        """
        for part in (accept_encoding or "").split(","):
            token, _, params = part.strip().partition(";")
            if token.strip().lower() not in ("gzip", "*"):
                continue
            for param in params.split(";"):
                key, _, value = param.partition("=")
                if key.strip().lower() == "q":
                    try:
                        if float(value.strip()) == 0:
                            return False
                    except ValueError:
                        return False  # a q we cannot read is not consent
            return True
        return False

    def _gzipped(self, body: bytes) -> bytes:
        """Compressed bytes for this exact content, computed at most once.

        KEYED BY CONTENT, NOT BY PATH AND MTIME. The SDK substitution above means the bytes
        served are not always the bytes of the file named in the request, so a (path, mtime) key
        can be stale in the one case that matters most — the script the page cannot start
        without. Hashing 476 KB costs well under a millisecond and removes the question.

        `mtime=0` so the output depends on the input alone: two identical assets produce one
        cache entry, and a rebuilt-but-unchanged file does not invalidate anything.
        """
        key = blake2b(body, digest_size=16).digest()
        hit = self._memo.get(key)
        if hit is not None:
            return hit

        packed = gzip.compress(body, compresslevel=6, mtime=0)

        if len(packed) <= _MEMO_MAX_BYTES:
            if len(self._memo) >= _MEMO_MAX_ENTRIES:
                del self._memo[next(iter(self._memo))]
            self._memo[key] = packed
        return packed
