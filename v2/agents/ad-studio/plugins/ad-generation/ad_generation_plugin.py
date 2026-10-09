"""ad-generation — the bundle's COMPOSITION ROOT.

The only place that wires anything: adapters -> catalog -> services -> tools, every class
constructor-injected. The services that think with the agent's model are built per call from a
factory, because the Reasoner wraps the calling tool's own model access.
"""

from __future__ import annotations

import secrets
import time

import imageio_ffmpeg
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
from ad_generation.application.one_time_approvals import OneTimeApprovals
from ad_generation.application.brand_service import BrandService
from ad_generation.application.cast_service import CastService
from ad_generation.application.collection_service import CollectionService
from ad_generation.application.design_reference_picker import DesignReferencePicker
from ad_generation.application.design_reference_service import DesignReferenceService
from ad_generation.application.design_service import DesignService
from ad_generation.application.design_template_service import DesignTemplateService
from ad_generation.application.post_page_check import PostPageCheck
from ad_generation.application.post_planner import PostPlanner
from ad_generation.application.post_service import PostService
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
from ad_generation.infrastructure.brand_file_store import BrandFileStore
from ad_generation.infrastructure.cast_file_library import CastFileLibrary
from ad_generation.infrastructure.collection_file_store import CollectionFileStore
from ad_generation.infrastructure.design_reference_file_store import DesignReferenceFileStore
from ad_generation.infrastructure.design_template_file_store import DesignTemplateFileStore
from ad_generation.infrastructure.ffmpeg_media_probe import FfmpegMediaProbe
from ad_generation.infrastructure.ffmpeg_slide_renderer import FfmpegSlideRenderer
from ad_generation.infrastructure.playwright_design_renderer import PlaywrightDesignRenderer
from ad_generation.infrastructure.post_file_store import PostFileStore
from ad_generation.infrastructure.proposal_file_store import ProposalFileStore
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
from ad_generation.presentation.brand_profile_tool import BrandProfileTool
from ad_generation.presentation.cast_approval_tool import CastApprovalTool
from ad_generation.presentation.collection_add_tool import CollectionAddTool
from ad_generation.presentation.collection_import_tool import CollectionImportTool
from ad_generation.presentation.collection_list_tool import CollectionListTool
from ad_generation.presentation.collection_update_tool import CollectionUpdateTool
from ad_generation.presentation.design_reference_delete_tool import DesignReferenceDeleteTool
from ad_generation.presentation.design_reference_list_tool import DesignReferenceListTool
from ad_generation.presentation.design_reference_save_tool import DesignReferenceSaveTool
from ad_generation.presentation.design_template_list_tool import DesignTemplateListTool
from ad_generation.presentation.design_template_save_tool import DesignTemplateSaveTool
from ad_generation.presentation.post_attach_tool import PostAttachTool
from ad_generation.presentation.post_design_tool import PostDesignTool
from ad_generation.presentation.post_list_tool import PostListTool
from ad_generation.presentation.post_render_tool import PostRenderTool
from ad_generation.presentation.post_replan_tool import PostReplanTool
from ad_generation.presentation.post_start_tool import PostStartTool
from ad_generation.presentation.post_update_tool import PostUpdateTool
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
    cast_approvals = OneTimeApprovals(ProposalFileStore(workspace, "cast/proposals.json"), lambda: secrets.token_hex(12))
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

    # Posts: collections gathered from any campaign -> an Instagram carousel or Reel, planned by the
    # agent's model from the playbook, rendered with Pillow + ffmpeg (no generation, nothing paid).
    collection_files = CollectionFileStore(workspace)
    collections = CollectionService(collection_files, store)
    brand_files = BrandFileStore(workspace)
    media_probe = FfmpegMediaProbe(workspace, imageio_ffmpeg.get_ffmpeg_exe())
    slide_renderer = FfmpegSlideRenderer(workspace, root / "fonts", imageio_ffmpeg.get_ffmpeg_exe(), media_probe)
    post_files = PostFileStore(workspace)
    # Designs: the agent's model writes each slide as HTML/CSS; a headless browser renders it.
    design_renderer = PlaywrightDesignRenderer(workspace, imageio_ffmpeg.get_ffmpeg_exe(), media_probe)
    design_templates = DesignTemplateFileStore(workspace, root / "design_templates")
    # References: pictures of designs worth following (Canva previews, screenshots), read once.
    design_references = DesignReferenceFileStore(workspace)

    def designs(reasoner, progress):
        return DesignService(
            reasoner, post_files, collection_files, brand_files, design_templates, design_references,
            DesignReferencePicker(reasoner, prompt("design_reference_pick")), design_renderer, slide_renderer,
            prompt("design_slide"), prompt("design_playbook"), prompt("design_critique"), progress, time.time,
        )

    def references(reasoner, progress):
        return DesignReferenceService(design_references, reasoner, prompt("design_reference_read"), time.time)

    def posts(reasoner, progress):
        planner = PostPlanner(reasoner, prompt("post_plan"), prompt("post_playbook")) if reasoner is not None else None
        pages = PostPageCheck(reasoner, prompt("post_page_check")) if reasoner is not None else None
        return PostService(post_files, collection_files, brand_files, store, slide_renderer, design_renderer, media_probe, planner, progress, pages)
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
    api.register_tool(CollectionAddTool(collections))
    api.register_tool(CollectionImportTool(collections))
    api.register_tool(CollectionListTool(collections, workspace))
    api.register_tool(CollectionUpdateTool(collections))
    api.register_tool(BrandProfileTool(BrandService(brand_files)))
    api.register_tool(PostStartTool(config, posts, vision_images))
    api.register_tool(PostReplanTool(config, posts, vision_images))
    api.register_tool(PostUpdateTool(posts))
    api.register_tool(PostRenderTool(posts))
    api.register_tool(PostAttachTool(config, posts, vision_images))
    api.register_tool(PostDesignTool(config, designs, vision_images))
    template_service = DesignTemplateService(design_templates, post_files)
    api.register_tool(DesignTemplateListTool(template_service, workspace))
    api.register_tool(DesignTemplateSaveTool(template_service))
    api.register_tool(DesignReferenceSaveTool(config, references, vision_images))
    reference_library = DesignReferenceService(design_references, None, "", time.time)
    api.register_tool(DesignReferenceListTool(reference_library, workspace))
    api.register_tool(DesignReferenceDeleteTool(reference_library))
    api.register_tool(PostListTool(posts, workspace))
    api.register_tool(RecipeListTool(views))
    api.register_tool(GenerationModelsTool(config, specs))
