"""ad-generation — the bundle's COMPOSITION ROOT.

The only place that wires anything: adapters -> catalog -> services -> tools, every class
constructor-injected. The services that think with the agent's model are built per call from a
factory, because the Reasoner wraps the calling tool's own model access.
"""

from __future__ import annotations

import time
from pathlib import Path

from ad_generation.application.animation_service import AnimationService
from ad_generation.application.brief_service import BriefService
from ad_generation.application.campaign_run_service import CampaignRunService
from ad_generation.application.campaign_view_service import CampaignViewService
from ad_generation.application.cast_service import CastService
from ad_generation.application.keyframe_service import KeyframeService
from ad_generation.application.product_analysis_service import ProductAnalysisService
from ad_generation.application.prompt_composer import PromptComposer
from ad_generation.application.quality_check_service import QualityCheckService
from ad_generation.application.shoot_sheet_service import ShootSheetService
from ad_generation.application.still_fix_service import StillFixService
from ad_generation.infrastructure.byteplus_ark_client import BytePlusArkClient
from ad_generation.infrastructure.byteplus_image_generator import BytePlusImageGenerator
from ad_generation.infrastructure.byteplus_video_generator import BytePlusVideoGenerator
from ad_generation.infrastructure.campaign_file_store import CampaignFileStore
from ad_generation.infrastructure.daemon_checkpoint_ledger import DaemonCheckpointLedger
from ad_generation.infrastructure.cast_file_library import CastFileLibrary
from ad_generation.infrastructure.fal_image_generator import FalImageGenerator
from ad_generation.infrastructure.fal_queue_client import FalQueueClient
from ad_generation.infrastructure.fal_video_generator import FalVideoGenerator
from ad_generation.infrastructure.format_file_library import FormatFileLibrary
from ad_generation.infrastructure.higgsfield_api_client import HiggsfieldApiClient
from ad_generation.infrastructure.higgsfield_image_generator import HiggsfieldImageGenerator
from ad_generation.infrastructure.higgsfield_session import HiggsfieldSession
from ad_generation.infrastructure.higgsfield_video_generator import HiggsfieldVideoGenerator
from ad_generation.infrastructure.image_data_uri_encoder import ImageDataUriEncoder
from ad_generation.infrastructure.media_downloader import MediaDownloader
from ad_generation.infrastructure.model_spec_book import ModelSpecBook
from ad_generation.infrastructure.price_calculator import PriceCalculator
from ad_generation.infrastructure.provider_generator_catalog import ProviderGeneratorCatalog
from ad_generation.infrastructure.recipe_file_library import RecipeFileLibrary
from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer
from ad_generation.presentation.campaign_ask_tool import CampaignAskTool
from ad_generation.presentation.campaign_brief_tool import CampaignBriefTool
from ad_generation.presentation.campaign_list_tool import CampaignListTool
from ad_generation.presentation.campaign_status_tool import CampaignStatusTool
from ad_generation.presentation.cast_list_tool import CastListTool
from ad_generation.presentation.campaign_run_tool import CampaignRunTool
from ad_generation.presentation.cast_create_tool import CastCreateTool
from ad_generation.presentation.generation_backend_resolver import provider_settings
from ad_generation.presentation.keyframe_generate_tool import KeyframeGenerateTool
from ad_generation.presentation.media_check_tool import MediaCheckTool
from ad_generation.presentation.product_analyze_tool import ProductAnalyzeTool
from ad_generation.presentation.shot_animate_tool import ShotAnimateTool
from ad_generation.presentation.still_fix_tool import StillFixTool
from ad_generation.presentation.video_models_tool import VideoModelsTool

# The longest an input image's side is sent at; providers downscale larger inputs anyway.
_INPUT_MAX_SIDE = 2048
_INPUT_JPEG_QUALITY = 92
# A 1080p, 10-second clip is tens of MB; this bounds a download, never truncates one.
_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
_IMAGE_TIMEOUT_S = 300.0
_VIDEO_TIMEOUT_S = 1200.0
# How many product photos ride along as references in a keyframe (they compete with the cast).
_MAX_PRODUCT_PHOTOS = 3
# What a vision model is shown: enough to judge a logo, a colour or a face, small enough that a
# check is a few hundred KB through the model proxy instead of ~10 MB.
_VISION_MAX_SIDE = 1024
_VISION_JPEG_QUALITY = 85


def register(api, ctx):
    root = Path(ctx.plugin_dir)
    config = ctx.config

    def prompt(name: str) -> str:
        return (root / "prompts" / f"{name}.md").read_text(encoding="utf-8")

    workspace = RunWorkspace()
    encoder = ImageDataUriEncoder(workspace, _INPUT_MAX_SIDE, _INPUT_JPEG_QUALITY)
    downloader = MediaDownloader(_MAX_DOWNLOAD_BYTES, timeout_s=600)
    specs = ModelSpecBook(root / "model_specs")
    prices = PriceCalculator()
    fal = FalQueueClient(poll_s=4)
    ark = BytePlusArkClient(poll_s=6)
    higgs_session = HiggsfieldSession(lambda: provider_settings(config, "higgsfield"))
    higgs = HiggsfieldApiClient(higgs_session, workspace, poll_s=6)
    generators = ProviderGeneratorCatalog(
        images={
            "fal": FalImageGenerator(fal, specs, encoder, downloader, prices, _IMAGE_TIMEOUT_S),
            "byteplus": BytePlusImageGenerator(ark, specs, encoder, downloader, prices, _IMAGE_TIMEOUT_S),
            "higgsfield": HiggsfieldImageGenerator(higgs, higgs_session, specs, downloader, _IMAGE_TIMEOUT_S),
        },
        videos={
            "fal": FalVideoGenerator(fal, specs, encoder, downloader, prices, _VIDEO_TIMEOUT_S),
            "byteplus": BytePlusVideoGenerator(ark, specs, encoder, downloader, prices, _VIDEO_TIMEOUT_S),
            "higgsfield": HiggsfieldVideoGenerator(higgs, higgs_session, specs, downloader, _VIDEO_TIMEOUT_S),
        },
    )
    store = CampaignFileStore(workspace)
    cast = CastFileLibrary(workspace)
    formats = FormatFileLibrary(root / "formats")
    recipes = RecipeFileLibrary(root / "recipes")
    approvals = DaemonCheckpointLedger(workspace)
    views = CampaignViewService(store, recipes, cast)
    keyframes = KeyframeService(generators, store, cast, _MAX_PRODUCT_PHOTOS)
    animation = AnimationService(generators, store, cast)

    def analysis(reasoner):
        return ProductAnalysisService(reasoner, store, recipes, prompt("product_analysis"))

    composer = PromptComposer(
        prompt("keyframe_cast"), prompt("keyframe_product"), prompt("motion_cast"), prompt("motion_product")
    )

    def briefs(reasoner):
        return BriefService(
            reasoner, store, formats, cast, composer, prompt("creative_brief"), prompt("recipe_brief")
        )

    def checks(reasoner):
        return QualityCheckService(reasoner, store, cast, prompt("media_check"))

    def sheets(check_reasoner):
        return ShootSheetService(generators, store, cast, checks(check_reasoner), prompt("shoot_sheet"))

    def still_fix(check_reasoner):
        return StillFixService(generators, store, cast, checks(check_reasoner), prompt("still_fix"), _MAX_PRODUCT_PHOTOS)

    def campaign_run(vision_reasoner, text_reasoner, check_reasoner, progress):
        return CampaignRunService(
            analysis(vision_reasoner), briefs(text_reasoner), keyframes, animation, sheets(check_reasoner),
            checks(check_reasoner), store, recipes, approvals, views, progress, time.time,
        )

    vision_images = VisionImagePreparer(workspace, _VISION_MAX_SIDE, _VISION_JPEG_QUALITY)
    api.register_tool(CampaignRunTool(config, campaign_run, vision_images, specs))
    api.register_tool(CampaignAskTool(config, store))
    # Read only — what the window draws. It acts by sending the user's answer as a message.
    api.register_tool(CampaignListTool(views, workspace))
    api.register_tool(CampaignStatusTool(views, workspace))
    api.register_tool(CastListTool(views, workspace))
    api.register_tool(VideoModelsTool(config, specs))
    api.register_tool(ProductAnalyzeTool(config, analysis, vision_images))
    api.register_tool(CampaignBriefTool(config, briefs, vision_images))
    api.register_tool(CastCreateTool(config, CastService(generators, cast, prompt("cast_sheet"))))
    api.register_tool(KeyframeGenerateTool(config, keyframes, store))
    api.register_tool(MediaCheckTool(config, checks, vision_images))
    api.register_tool(ShotAnimateTool(config, animation, store))
    api.register_tool(StillFixTool(config, still_fix, store, vision_images))
