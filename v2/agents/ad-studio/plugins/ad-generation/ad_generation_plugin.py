"""ad-generation — the bundle's COMPOSITION ROOT.

The only place that wires anything: adapters -> catalog -> services -> tools, every class
constructor-injected. The services that think with the agent's model are built per call from a
factory, because the Reasoner wraps the calling tool's own model access.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from ad_generation.application.animation_service import AnimationService
from ad_generation.application.brief_service import BriefService
from ad_generation.application.campaign_checklist_editor import CampaignChecklistEditor
from ad_generation.application.campaign_checklist_loader import CampaignChecklistLoader
from ad_generation.application.campaign_step_service import CampaignStepService
from ad_generation.application.step_defaults import StepDefaults
from ad_generation.application.step_media import StepMedia
from ad_generation.application.campaign_view_service import CampaignViewService
from ad_generation.application.run_approvals import RunApprovals
from ad_generation.application.clip_edit_service import ClipEditService
from ad_generation.application.cast_approvals import CastApprovals
from ad_generation.application.cast_service import CastService
from ad_generation.application.keyframe_service import KeyframeService
from ad_generation.application.product_analysis_service import ProductAnalysisService
from ad_generation.application.product_sheet_service import ProductSheetService
from ad_generation.application.poster_brief_service import PosterBriefService
from ad_generation.application.poster_prompt_composer import PosterPromptComposer
from ad_generation.application.poster_text_service import PosterTextService
from ad_generation.application.prompt_composer import PromptComposer
from ad_generation.application.quality_check_service import QualityCheckService
from ad_generation.application.shoot_sheet_service import ShootSheetService
from ad_generation.application.still_fix_service import StillFixService
from ad_generation.infrastructure.byteplus_ark_client import BytePlusArkClient
from ad_generation.infrastructure.byteplus_image_generator import BytePlusImageGenerator
from ad_generation.infrastructure.byteplus_video_generator import BytePlusVideoGenerator
from ad_generation.infrastructure.campaign_file_store import CampaignFileStore
from ad_generation.infrastructure.cast_file_library import CastFileLibrary
from ad_generation.infrastructure.cast_proposal_file_store import CastProposalFileStore
from ad_generation.infrastructure.fal_image_generator import FalImageGenerator
from ad_generation.infrastructure.fal_queue_client import FalQueueClient
from ad_generation.infrastructure.fal_video_generator import FalVideoGenerator
from ad_generation.infrastructure.format_file_library import FormatFileLibrary
from ad_generation.infrastructure.higgsfield_api_client import HiggsfieldApiClient
from ad_generation.infrastructure.higgsfield_image_generator import HiggsfieldImageGenerator
from ad_generation.infrastructure.higgsfield_session import HiggsfieldSession
from ad_generation.infrastructure.higgsfield_video_editor import HiggsfieldVideoEditor
from ad_generation.infrastructure.higgsfield_video_generator import HiggsfieldVideoGenerator
from ad_generation.infrastructure.image_data_uri_encoder import ImageDataUriEncoder
from ad_generation.infrastructure.media_downloader import MediaDownloader
from ad_generation.infrastructure.model_spec_book import ModelSpecBook
from ad_generation.infrastructure.opencv_clip_frames import OpenCvClipFrames
from ad_generation.infrastructure.pillow_image_resizer import PillowImageResizer
from ad_generation.infrastructure.pillow_media_previewer import PillowMediaPreviewer
from ad_generation.infrastructure.pillow_poster_typesetter import PillowPosterTypesetter
from ad_generation.infrastructure.pillow_sheet_panels import PillowSheetPanels
from ad_generation.infrastructure.poster_text_file_store import PosterTextFileStore
from ad_generation.infrastructure.price_calculator import PriceCalculator
from ad_generation.infrastructure.provider_generator_catalog import ProviderGeneratorCatalog
from ad_generation.infrastructure.recipe_file_library import RecipeFileLibrary
from ad_generation.infrastructure.run_workspace import RunWorkspace
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer
from ad_generation.infrastructure.yunet_face_crops import YuNetFaceCrops
from ad_generation.presentation.campaign_settings_tool import CampaignSettingsTool
from ad_generation.presentation.campaign_start_tool import CampaignStartTool
from ad_generation.presentation.run_approval_tool import RunApprovalTool
from ad_generation.presentation.generation_choice_validator import GenerationChoiceValidator
from ad_generation.presentation.recipe_list_tool import RecipeListTool
from ad_generation.presentation.step_add_tool import StepAddTool
from ad_generation.presentation.step_import_tool import StepImportTool
from ad_generation.presentation.step_pick_tool import StepPickTool
from ad_generation.presentation.step_run_tool import StepRunTool
from ad_generation.presentation.step_update_tool import StepUpdateTool
from ad_generation.presentation.campaign_generations_tool import CampaignGenerationsTool
from ad_generation.presentation.campaign_list_tool import CampaignListTool
from ad_generation.presentation.campaign_status_tool import CampaignStatusTool
from ad_generation.presentation.cast_list_tool import CastListTool
from ad_generation.presentation.cast_approval_tool import CastApprovalTool
from ad_generation.presentation.poster_text_tool import PosterTextTool
from ad_generation.presentation.cast_create_tool import CastCreateTool
from ad_generation.presentation.clip_edit_tool import ClipEditTool
from ad_generation.presentation.generation_backend_resolver import provider_settings
from ad_generation.presentation.media_check_tool import MediaCheckTool
from ad_generation.presentation.still_fix_tool import StillFixTool
from ad_generation.presentation.generation_models_tool import GenerationModelsTool

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
# A result's preview in the chat: enough to see it, small enough to re-send with every turn.
_PREVIEW_MAX_SIDE = 768
_PREVIEW_JPEG_QUALITY = 80


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
            "higgsfield": HiggsfieldVideoGenerator(higgs, higgs_session, specs, downloader, workspace, _VIDEO_TIMEOUT_S),
        },
        editors={
            "higgsfield": HiggsfieldVideoEditor(higgs, higgs_session, specs, downloader, _VIDEO_TIMEOUT_S),
        },
    )
    store = CampaignFileStore(workspace)
    frames = OpenCvClipFrames(workspace)
    cast = CastFileLibrary(workspace)
    formats = FormatFileLibrary(root / "formats")
    recipes = RecipeFileLibrary(root / "recipes")
    loader = CampaignChecklistLoader(store, recipes)
    faces = YuNetFaceCrops(workspace, root / "models" / "face_detection_yunet_2023mar.onnx")
    defaults = StepDefaults(store, cast, faces, PillowSheetPanels(workspace), _MAX_PRODUCT_PHOTOS)
    previews = PillowMediaPreviewer(workspace, _PREVIEW_MAX_SIDE, _PREVIEW_JPEG_QUALITY)
    media = StepMedia(store)
    editor = CampaignChecklistEditor(store, recipes, loader, media)
    approvals = RunApprovals(store, loader, lambda: secrets.token_hex(12))
    cast_approvals = CastApprovals(CastProposalFileStore(workspace), lambda: secrets.token_hex(12))
    poster_texts = PosterTextFileStore(workspace)
    poster_text = PosterTextService(store, poster_texts, PillowPosterTypesetter(workspace, root / "fonts"))
    views = CampaignViewService(store, recipes, cast, loader, defaults, media, poster_texts)
    keyframes = KeyframeService(generators, store, defaults, prompt("photo_style_person"), prompt("photo_style_product"))
    animation = AnimationService(generators, store, defaults)

    def analysis(reasoner):
        return ProductAnalysisService(reasoner, store, recipes, prompt("product_analysis"))

    composer = PromptComposer(
        prompt("keyframe_cast"), prompt("keyframe_product"), prompt("motion_cast"), prompt("motion_product")
    )

    def briefs(reasoner):
        return BriefService(reasoner, store, formats, cast, composer, prompt("recipe_brief"))

    poster_composer = PosterPromptComposer(prompt("keyframe_poster"), prompt("motion_poster"), prompt("keyframe_poster_overlay"))

    def posters(reasoner):
        return PosterBriefService(reasoner, store, formats, poster_composer, prompt("poster_brief"))

    def checks(reasoner):
        return QualityCheckService(reasoner, store, cast, prompt("media_check"))

    def sheets(check_reasoner):
        return ShootSheetService(generators, store, cast, checks(check_reasoner), prompt("shoot_sheet"))

    def still_fix(check_reasoner):
        return StillFixService(
            generators, store, cast, loader, defaults, checks(check_reasoner), prompt("still_fix"), _MAX_PRODUCT_PHOTOS,
            PillowImageResizer(workspace),
        )

    def clip_edit(check_reasoner):
        return ClipEditService(generators, store, loader, defaults, frames, checks(check_reasoner))

    def product_sheets(check_reasoner):
        return ProductSheetService(generators, store, checks(check_reasoner), prompt("product_sheet"), _MAX_PRODUCT_PHOTOS)

    def steps(vision_reasoner, text_reasoner, check_reasoner, progress):
        return CampaignStepService(
            analysis(vision_reasoner), briefs(text_reasoner), posters(text_reasoner), sheets(check_reasoner),
            product_sheets(check_reasoner),
            keyframes, animation,
            checks(check_reasoner), frames, store, recipes, loader, defaults, poster_text, progress,
        )

    vision_images = VisionImagePreparer(workspace, _VISION_MAX_SIDE, _VISION_JPEG_QUALITY)
    validator = GenerationChoiceValidator(specs)
    # A campaign: started from its recipe, then any step run, re-run, added, changed, picked.
    api.register_tool(CampaignStartTool(config, steps, views, vision_images))
    api.register_tool(StepRunTool(config, steps, loader, views, validator, previews, approvals, vision_images))
    api.register_tool(StepPickTool(editor))
    api.register_tool(PosterTextTool(poster_text))
    api.register_tool(StepImportTool(editor))
    api.register_tool(StepAddTool(editor))
    api.register_tool(StepUpdateTool(editor))
    api.register_tool(StillFixTool(config, still_fix, store, previews, approvals, vision_images))
    api.register_tool(ClipEditTool(config, clip_edit, store, approvals, vision_images))
    # The studio's own controls: approve a run (window only), and the approval switch (window only).
    api.register_tool(RunApprovalTool(approvals))
    api.register_tool(CampaignSettingsTool(approvals))
    api.register_tool(MediaCheckTool(config, checks, loader, frames, vision_images))
    api.register_tool(CastCreateTool(config, CastService(generators, cast, prompt("cast_sheet")), cast_approvals))
    api.register_tool(CastApprovalTool(cast_approvals))
    # Read only — what the window draws (step_pick is the one thing it changes, directly: free).
    api.register_tool(CampaignListTool(views, workspace))
    api.register_tool(CampaignStatusTool(views, workspace))
    api.register_tool(CampaignGenerationsTool(views, workspace))
    api.register_tool(CastListTool(views, cast_approvals, workspace))
    api.register_tool(RecipeListTool(views))
    api.register_tool(GenerationModelsTool(config, specs))
