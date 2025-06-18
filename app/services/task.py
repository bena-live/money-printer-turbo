import math
import os
import os.path
import re
import time
import json
from os import path

from loguru import logger

from app.config import config
from app.models import const
from app.models.schema import VideoConcatMode, VideoParams
from app.services import llm, material, subtitle, video, voice
from app.services import state as sm
from app.utils import utils


def generate_script(task_id, params):
    logger.info("\n\n## generating video script")
    video_script = params.video_script.strip()
    if not video_script:
        video_script = llm.generate_script(
            video_subject=params.video_subject,
            language=params.video_language,
            paragraph_number=params.paragraph_number,
        )
    else:
        logger.debug(f"video script: \n{video_script}")

    if not video_script:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error("failed to generate video script.")
        return None

    return video_script


def generate_test_script(task_id, params):
    logger.info("\n\n## generating similar script based on original")
    video_origin_script = params.video_script.strip()
    video_script = llm.generate_similar_script(
        original_script=video_origin_script,
        language=params.video_language,
        paragraph_number=params.paragraph_number,
    )
    if not video_script:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error("failed to generate similar script.")
        return None
    
    return video_script

def generate_terms(task_id, params, video_script):
    logger.info("\n\n## generating video terms")
    video_terms = params.video_terms
    if not video_terms:
        video_terms = llm.generate_terms(
            video_subject=params.video_subject, video_script=video_script, amount=5
        )
    else:
        if isinstance(video_terms, str):
            video_terms = [term.strip() for term in re.split(r"[,，]", video_terms)]
        elif isinstance(video_terms, list):
            video_terms = [term.strip() for term in video_terms]
        else:
            raise ValueError("video_terms must be a string or a list of strings.")

        logger.debug(f"video terms: {utils.to_json(video_terms)}")

    if not video_terms:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error("failed to generate video terms.")
        return None

    return video_terms


def save_script_data(task_id, video_script, video_terms, params):
    script_file = path.join(utils.task_dir(task_id), "script.json")
    script_data = {
        "script": video_script,
        "search_terms": video_terms,
        "params": params,
    }

    with open(script_file, "w", encoding="utf-8") as f:
        f.write(utils.to_json(script_data))


def generate_audio(task_id, params, video_script):
    logger.info("\n\n## generating audio")
    audio_file = path.join(utils.task_dir(task_id), "audio.mp3")
    sub_maker = voice.tts(
        text=video_script,
        voice_name=voice.parse_voice_name(params.voice_name),
        voice_rate=params.voice_rate,
        voice_file=audio_file,
    )
    if sub_maker is None:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error(
            """failed to generate audio:
1. check if the language of the voice matches the language of the video script.
2. check if the network is available. If you are in China, it is recommended to use a VPN and enable the global traffic mode.
        """.strip()
        )
        return None, None, None

    audio_duration = math.ceil(voice.get_audio_duration(sub_maker))
    return audio_file, audio_duration, sub_maker

def generate_test_audio(task_id, params, video_script):
    logger.info("\n\n## generating audio using ElevenLabs")
    audio_file = path.join(utils.task_dir(task_id), "audio.mp3")
    
    # ElevenLabs 参数
    api_key = "950572cfc2d8f7b6a3515b9fb2b16f9e"
    voice_id = "MOevUawCfvCOEYqi1iu8"
    
    # model_id: 模型ID，默认 "eleven_multilingual_v2"，其他选项包括 "eleven_monolingual_v1"
    # voice_stability: 语音稳定性 (0.0-1.0)，默认 0.5
    # voice_similarity_boost: 语音相似度增强 (0.0-1.0)，默认 0.5
    # voice_style: 语音风格强度 (0.0-1.0)，默认 0.0，仅部分语音支持
    # voice_use_speaker_boost: 是否使用说话者增强，默认 True
    
    sub_maker = voice.elevenlabs_tts(
        text=video_script,
        api_key=api_key,
        voice_id=voice_id,
        voice_file=audio_file,
        # model_id="eleven_multilingual_v2",
        # voice_stability=0.5,
        # voice_similarity_boost=0.5,
        # voice_style=0.0,
        # voice_use_speaker_boost=True,
    )
    
    if sub_maker is None:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        logger.error(
            """failed to generate audio using ElevenLabs:
1. check if the API key is valid
2. check if the voice_id is correct
3. check if the network is available
        """.strip()
        )
        return None, None, None

    audio_duration = math.ceil(voice.get_audio_duration(sub_maker))
    return audio_file, audio_duration, sub_maker

def generate_subtitle(task_id, params, video_script, sub_maker, audio_file):
    if not params.subtitle_enabled:
        return ""

    subtitle_path = path.join(utils.task_dir(task_id), "subtitle.srt")
    subtitle_provider = config.app.get("subtitle_provider", "edge").strip().lower()
    logger.info(f"\n\n## generating subtitle, provider: {subtitle_provider}")

    subtitle_fallback = False
    if subtitle_provider == "edge":
        voice.create_subtitle(
            text=video_script, sub_maker=sub_maker, subtitle_file=subtitle_path
        )
        if not os.path.exists(subtitle_path):
            subtitle_fallback = True
            logger.warning("subtitle file not found, fallback to whisper")

    if subtitle_provider == "whisper" or subtitle_fallback:
        subtitle.create(audio_file=audio_file, subtitle_file=subtitle_path)
        logger.info("\n\n## correcting subtitle")
        subtitle.correct(subtitle_file=subtitle_path, video_script=video_script)

    subtitle_lines = subtitle.file_to_subtitles(subtitle_path)
    if not subtitle_lines:
        logger.warning(f"subtitle file is invalid: {subtitle_path}")
        return ""

    return subtitle_path


def get_video_materials(task_id, params, video_terms, audio_duration):
    if params.video_source == "local":
        logger.info("\n\n## preprocess local materials")
        # materials = video.preprocess_video(
        #     materials=params.video_materials, clip_duration=params.video_clip_duration
        # )
        materials = video.get_local_videos()
        if not materials:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error(
                "no valid materials found, please check the materials and try again."
            )
            return None
        return [material_info.url for material_info in materials]
    else:
        logger.info(f"\n\n## downloading videos from {params.video_source}")
        downloaded_videos = material.download_videos(
            task_id=task_id,
            search_terms=video_terms,
            source=params.video_source,
            video_aspect=params.video_aspect,
            video_contact_mode=params.video_concat_mode,
            audio_duration=audio_duration * params.video_count,
            max_clip_duration=params.video_clip_duration,
        )
        if not downloaded_videos:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            logger.error(
                "failed to download videos, maybe the network is not available. if you are in China, please use a VPN."
            )
            return None
        return downloaded_videos


def save_default_title(video_path, default_title):
    """
    在视频文件相同路径下创建同名的 .txt 文件，写入 default_title
    
    Args:
        video_path: 视频文件的完整路径
        default_title: 要写入的默认标题内容
    """
    if not default_title:
        return
    
    # 获取视频文件的目录和文件名（不含扩展名）
    video_dir = os.path.dirname(video_path)
    video_filename = os.path.basename(video_path)
    filename_without_ext = os.path.splitext(video_filename)[0]
    
    # 构建 .txt 文件路径
    txt_file_path = os.path.join(video_dir, f"{filename_without_ext}.txt")
    
    # 写入 default_title 到文件
    try:
        with open(txt_file_path, 'w', encoding='utf-8') as f:
            f.write(default_title)
        logger.info(f"Saved default title to: {txt_file_path}")
    except Exception as e:
        logger.error(f"Failed to save default title: {e}")


def generate_final_videos(
    task_id, params, downloaded_videos, audio_file, subtitle_path, index, target_folder=None
):
    video_concat_mode = (
        params.video_concat_mode if params.video_count == 1 else VideoConcatMode.random
    )
    video_transition_mode = params.video_transition_mode

    _progress = 50
    combined_video_path = path.join(
        utils.task_dir(task_id), f"combined-{index}.mp4"
    )
    logger.info(f"\n\n## combining video: {index} => {combined_video_path}")
    video.combine_videos(
        combined_video_path=combined_video_path,
        video_paths=downloaded_videos,
        audio_file=audio_file,
        video_aspect=params.video_aspect,
        video_concat_mode=video_concat_mode,
        video_transition_mode=video_transition_mode,
        max_clip_duration=params.video_clip_duration,
        threads=params.n_threads,
    )

    _progress += 50 / params.video_count / 2
    sm.state.update_task(task_id, progress=_progress)

    # 使用当前时间毫秒数作为文件名
    timestamp_ms = int(time.time() * 1000)
    
    # 如果指定了目标文件夹，则保存到指定文件夹
    if target_folder:
        os.makedirs(target_folder, exist_ok=True)
        final_video_path = path.join(target_folder, f"{index}-{timestamp_ms}.mp4")
    else:
        final_video_path = path.join(utils.task_dir(task_id), f"{index}-{timestamp_ms}.mp4")

    logger.info(f"\n\n## generating video: {index} => {final_video_path}")
    video.generate_video(
        video_path=combined_video_path,
        audio_path=audio_file,
        subtitle_path=subtitle_path,
        output_file=final_video_path,
        params=params,
    )

    _progress += 50 / params.video_count / 2
    sm.state.update_task(task_id, progress=_progress)

    return final_video_path, combined_video_path


def start(task_id, params: VideoParams, stop_at: str = "video"):
    logger.info(f"start task: {task_id}, stop_at: {stop_at}")
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=1)
    
    # 1. 更新 resource/config.json 文件
    selected_folders = getattr(params, 'selected_folders', [])
    folder_file_number = getattr(params, 'folder_file_number', 2)
    
    if selected_folders:
        resource_config_path = path.join(path.dirname(path.dirname(path.dirname(path.abspath(__file__)))), "resource", "config.json")
        if os.path.exists(resource_config_path):
            with open(resource_config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            # 更新默认选择的文件夹和文件数量
            config_data['default'] = selected_folders
            config_data['folder_file_number'] = folder_file_number
            
            with open(resource_config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Updated resource/config.json with selected_folders: {selected_folders} and folder_file_number: {folder_file_number}")
    
    # 2. 判断 selected_folders 并调整 video_count
    if selected_folders:
        original_video_count = params.video_count
        params.video_count = len(selected_folders) * folder_file_number
        logger.info(f"Adjusted video_count from {original_video_count} to {params.video_count} based on selected_folders")

    # 3. Generate terms
    video_terms = ""
    if params.video_source != "local":
        video_terms = generate_terms(task_id, params, params.video_script)
        if not video_terms:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return

    save_script_data(task_id, params.video_script, video_terms, params)

    if stop_at == "terms":
        sm.state.update_task(
            task_id, state=const.TASK_STATE_COMPLETE, progress=100, terms=video_terms
        )
        return {"script": params.video_script, "terms": video_terms}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=20)
    # 5. Get video materials
    downloaded_videos = get_video_materials(
        task_id, params, video_terms, audio_duration=0
    )
    if not downloaded_videos:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return

    if stop_at == "materials":
        sm.state.update_task(
            task_id,
            state=const.TASK_STATE_COMPLETE,
            progress=100,
            materials=downloaded_videos,
        )
        return {"materials": downloaded_videos}

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=5)

    if type(params.video_concat_mode) is str:
        params.video_concat_mode = VideoConcatMode(params.video_concat_mode)
    
    final_video_paths = []
    combined_video_paths = []
    
    # 4. 创建文件夹计数器
    folder_counters = {}
    if selected_folders:
        for folder in selected_folders:
            folder_counters[folder] = 0
    
    for i in range(params.video_count):
        # 1. Generate script
        # video_script = generate_script(task_id, params)
        video_script = generate_test_script(task_id, params)
        if not video_script or "Error: " in video_script:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return

        sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=10)

        if stop_at == "script":
            sm.state.update_task(
                task_id, state=const.TASK_STATE_COMPLETE, progress=100, script=video_script
            )
            return {"script": video_script}

        # 3. Generate audio
        # audio_file, audio_duration, sub_maker = generate_audio(
        #     task_id, params, video_script
        # )
        audio_file, audio_duration, sub_maker = generate_test_audio(
            task_id, params, video_script
        )
        
        if not audio_file:
            sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
            return

        sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=30)

        if stop_at == "audio":
            sm.state.update_task(
                task_id,
                state=const.TASK_STATE_COMPLETE,
                progress=100,
                audio_file=audio_file,
            )
            return {"audio_file": audio_file, "audio_duration": audio_duration}

        # 4. Generate subtitle
        subtitle_path = generate_subtitle(
            task_id, params, video_script, sub_maker, audio_file
        )

        if stop_at == "subtitle":
            sm.state.update_task(
                task_id,
                state=const.TASK_STATE_COMPLETE,
                progress=100,
                subtitle_path=subtitle_path,
            )
            return {"subtitle_path": subtitle_path}

        sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=40)

        # 5. 确定目标文件夹
        target_folder = None
        if selected_folders:
            # 找到当前应该使用的文件夹
            for folder in selected_folders:
                if folder_counters[folder] < folder_file_number:
                    azure_local_path = config.app.get("azure_local_path", "")
                    target_folder = path.join(azure_local_path, folder)
                    folder_counters[folder] += 1
                    logger.info(f"Video {i+1} will be saved to folder: {target_folder}, count: {folder_counters[folder]}/{folder_file_number}")
                    break

        # 6. Generate final videos
        final_video_path, combined_video_path = generate_final_videos(
            task_id, params, downloaded_videos, audio_file, subtitle_path, i + 1, target_folder
        )
        
        # 7. 如果有 default_title，创建对应的 .txt 文件
        default_title = getattr(params, 'default_title', '')
        if default_title and final_video_path:
            save_default_title(final_video_path, default_title)

        final_video_paths.append(final_video_path)
        combined_video_paths.append(combined_video_path)

    # ------------- for ↑↑↑ -------------

    if not final_video_paths:
        sm.state.update_task(task_id, state=const.TASK_STATE_FAILED)
        return

    logger.success(
        f"task {task_id} finished, generated {len(final_video_paths)} videos."
    )

    kwargs = {
        "videos": final_video_paths,
        "combined_videos": combined_video_paths,
        "script": video_script,
        "terms": video_terms,
        "audio_file": audio_file,
        "audio_duration": audio_duration,
        "subtitle_path": subtitle_path,
        "materials": downloaded_videos,
    }
    sm.state.update_task(
        task_id, state=const.TASK_STATE_COMPLETE, progress=100, **kwargs
    )
    return kwargs


if __name__ == "__main__":
    task_id = "task_id"
    params = VideoParams(
        video_subject="金钱的作用",
        voice_name="zh-CN-XiaoyiNeural-Female",
        voice_rate=1.0,
    )
    start(task_id, params, stop_at="video")
