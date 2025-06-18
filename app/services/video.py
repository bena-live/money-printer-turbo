import glob
import itertools
import os
import random
import gc
import shutil
from typing import List
from loguru import logger

# 配置 MoviePy 日志以禁止不必要的输出
from app.utils.moviepy_logger import init_moviepy_logger, suppress_moviepy_output
init_moviepy_logger()

from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    VideoFileClip,
    afx,
    concatenate_videoclips,
)
from moviepy.video.tools.subtitles import SubtitlesClip
from PIL import ImageFont

from app.models import const
from app.models.schema import (
    MaterialInfo,
    VideoAspect,
    VideoConcatMode,
    VideoParams,
    VideoTransitionMode,
)
from app.services.utils import video_effects
from app.utils import utils

class SubClippedVideoClip:
    def __init__(self, file_path, start_time=None, end_time=None, width=None, height=None, duration=None):
        self.file_path = file_path
        self.start_time = start_time
        self.end_time = end_time
        self.width = width
        self.height = height
        if duration is None:
            self.duration = end_time - start_time
        else:
            self.duration = duration

    def __str__(self):
        return f"SubClippedVideoClip(file_path={self.file_path}, start_time={self.start_time}, end_time={self.end_time}, duration={self.duration}, width={self.width}, height={self.height})"


audio_codec = "aac"
video_codec = "libx264"
fps = 30

def close_clip(clip):
    if clip is None:
        return
        
    try:
        # close main resources
        if hasattr(clip, 'reader') and clip.reader is not None:
            clip.reader.close()
            
        # close audio resources
        if hasattr(clip, 'audio') and clip.audio is not None:
            if hasattr(clip.audio, 'reader') and clip.audio.reader is not None:
                clip.audio.reader.close()
            del clip.audio
            
        # close mask resources
        if hasattr(clip, 'mask') and clip.mask is not None:
            if hasattr(clip.mask, 'reader') and clip.mask.reader is not None:
                clip.mask.reader.close()
            del clip.mask
            
        # handle child clips in composite clips
        if hasattr(clip, 'clips') and clip.clips:
            for child_clip in clip.clips:
                if child_clip is not clip:  # avoid possible circular references
                    close_clip(child_clip)
            
        # clear clip list
        if hasattr(clip, 'clips'):
            clip.clips = []
            
    except Exception as e:
        logger.error(f"failed to close clip: {str(e)}")
    
    del clip
    gc.collect()

def delete_files(files: List[str] | str):
    if isinstance(files, str):
        files = [files]
        
    for file in files:
        try:
            os.remove(file)
        except:
            pass

def get_bgm_file(bgm_type: str = "random", bgm_file: str = ""):
    if not bgm_type:
        return ""

    if bgm_file and os.path.exists(bgm_file):
        return bgm_file

    if bgm_type == "random":
        suffix = "*.mp3"
        song_dir = utils.song_dir()
        files = glob.glob(os.path.join(song_dir, suffix))
        return random.choice(files)

    return ""

def random_to_begin_video(
    max_clip_duration: int = 5,
):
    begin_videos_dir = utils.storage_dir("begin_videos", create=True)
    begin_list = [ "1.mp4", "2.mp4", "3.mp4", "4.mp4", "5.mp4" ]
    random_path = os.path.join(begin_videos_dir, random.choice(begin_list))
    logger.info(f"random begin video: {random_path}")

    with suppress_moviepy_output():
        clip = VideoFileClip(random_path)
        clip_duration = clip.duration
        clip_w, clip_h = clip.size
        close_clip(clip)
    end_time = min(max_clip_duration, clip_duration)
    begin_videoclip = SubClippedVideoClip(file_path= random_path, start_time=0, end_time=end_time, width=clip_w, height=clip_h)
    return begin_videoclip

def combine_videos(
    combined_video_path: str,
    video_paths: List[str],
    audio_file: str,
    video_aspect: VideoAspect = VideoAspect.portrait,
    video_concat_mode: VideoConcatMode = VideoConcatMode.random,
    video_transition_mode: VideoTransitionMode = None,
    max_clip_duration: int = 5,
    threads: int = 2,
) -> str:
    audio_clip = AudioFileClip(audio_file)
    audio_duration = audio_clip.duration
    logger.info(f"audio duration: {audio_duration} seconds")
    # Required duration of each clip
    req_dur = audio_duration / len(video_paths)
    req_dur = max_clip_duration
    logger.info(f"maximum clip duration: {req_dur} seconds")
    output_dir = os.path.dirname(combined_video_path)

    aspect = VideoAspect(video_aspect)
    video_width, video_height = aspect.to_resolution()

    processed_clips = []
    subclipped_items = []
    video_duration = 0
    for video_path in video_paths:
        with suppress_moviepy_output():
            clip = VideoFileClip(video_path)
            clip_duration = clip.duration
            clip_w, clip_h = clip.size
            close_clip(clip)
        
        start_time = 0

        while start_time < clip_duration:
            end_time = min(start_time + max_clip_duration, clip_duration)            
            if clip_duration - start_time >= max_clip_duration:
                subclipped_items.append(SubClippedVideoClip(file_path= video_path, start_time=start_time, end_time=end_time, width=clip_w, height=clip_h))
            start_time = end_time    
            if video_concat_mode.value == VideoConcatMode.sequential.value:
                break

    # random subclipped_items order
    if video_concat_mode.value == VideoConcatMode.random.value:
        random.shuffle(subclipped_items)
    begin_video = random_to_begin_video()
    subclipped_items.insert(0, begin_video)

    logger.debug(f"total subclipped items: {len(subclipped_items)}")
    
    # Add downloaded clips over and over until the duration of the audio (max_duration) has been reached
    for i, subclipped_item in enumerate(subclipped_items):
        if video_duration > audio_duration:
            break
        
        logger.debug(f"processing clip {i+1}: {subclipped_item.width}x{subclipped_item.height}, current duration: {video_duration:.2f}s, remaining: {audio_duration - video_duration:.2f}s")
        
        try:
            with suppress_moviepy_output():
                clip = VideoFileClip(subclipped_item.file_path).subclipped(subclipped_item.start_time, subclipped_item.end_time)
            clip_duration = clip.duration
            # Not all videos are same size, so we need to resize them
            clip_w, clip_h = clip.size
            if clip_w != video_width or clip_h != video_height:
                clip_ratio = clip.w / clip.h
                video_ratio = video_width / video_height
                logger.debug(f"resizing clip, source: {clip_w}x{clip_h}, ratio: {clip_ratio:.2f}, target: {video_width}x{video_height}, ratio: {video_ratio:.2f}")
                
                if clip_ratio == video_ratio:
                    clip = clip.resized(new_size=(video_width, video_height))
                else:
                    # 使用裁剪模式：将视频放大并裁剪，而不是添加黑边
                    # 计算缩放因子，确保视频充满整个画面
                    if clip_ratio > video_ratio:
                        # 视频更宽，基于高度缩放
                        scale_factor = video_height / clip_h
                    else:
                        # 视频更高，基于宽度缩放
                        scale_factor = video_width / clip_w
                    
                    # 缩放视频
                    new_width = int(clip_w * scale_factor)
                    new_height = int(clip_h * scale_factor)
                    clip = clip.resized(new_size=(new_width, new_height))
                    
                    # 裁剪到目标尺寸，保持中心位置
                    x_center = new_width // 2
                    y_center = new_height // 2
                    x1 = x_center - video_width // 2
                    y1 = y_center - video_height // 2
                    clip = clip.cropped(x1=x1, y1=y1, x2=x1 + video_width, y2=y1 + video_height)
                    
            shuffle_side = random.choice(["left", "right", "top", "bottom"])
            if video_transition_mode.value == VideoTransitionMode.none.value:
                clip = clip
            elif video_transition_mode.value == VideoTransitionMode.fade_in.value:
                clip = video_effects.fadein_transition(clip, 1)
            elif video_transition_mode.value == VideoTransitionMode.fade_out.value:
                clip = video_effects.fadeout_transition(clip, 1)
            elif video_transition_mode.value == VideoTransitionMode.slide_in.value:
                clip = video_effects.slidein_transition(clip, 1, shuffle_side)
            elif video_transition_mode.value == VideoTransitionMode.slide_out.value:
                clip = video_effects.slideout_transition(clip, 1, shuffle_side)
            elif video_transition_mode.value == VideoTransitionMode.shuffle.value:
                transition_funcs = [
                    lambda c: video_effects.fadein_transition(c, 1),
                    lambda c: video_effects.fadeout_transition(c, 1),
                    lambda c: video_effects.slidein_transition(c, 1, shuffle_side),
                    lambda c: video_effects.slideout_transition(c, 1, shuffle_side),
                ]
                shuffle_transition = random.choice(transition_funcs)
                clip = shuffle_transition(clip)

            if clip.duration > max_clip_duration:
                clip = clip.subclipped(0, max_clip_duration)
                
            # wirte clip to temp file
            clip_file = f"{output_dir}/temp-clip-{i+1}.mp4"
            clip.write_videofile(clip_file, logger=None, fps=fps, codec=video_codec)
            
            close_clip(clip)
        
            processed_clips.append(SubClippedVideoClip(file_path=clip_file, duration=clip.duration, width=clip_w, height=clip_h))
            video_duration += clip.duration
            
        except Exception as e:
            logger.error(f"failed to process clip: {str(e)}")
    
    # loop processed clips until the video duration matches or exceeds the audio duration.
    if video_duration < audio_duration:
        logger.warning(f"video duration ({video_duration:.2f}s) is shorter than audio duration ({audio_duration:.2f}s), looping clips to match audio length.")
        base_clips = processed_clips.copy()
        for clip in itertools.cycle(base_clips):
            if video_duration >= audio_duration:
                break
            processed_clips.append(clip)
            video_duration += clip.duration
        logger.info(f"video duration: {video_duration:.2f}s, audio duration: {audio_duration:.2f}s, looped {len(processed_clips)-len(base_clips)} clips")
     
    # merge video clips using batch processing to improve performance
    logger.info("starting clip merging process")
    if not processed_clips:
        logger.warning("no clips available for merging")
        return combined_video_path
    
    # if there is only one clip, use it directly
    if len(processed_clips) == 1:
        logger.info("using single clip directly")
        shutil.copy(processed_clips[0].file_path, combined_video_path)
        delete_files([processed_clips[0].file_path])
        logger.info("video combining completed")
        return combined_video_path
    
    # batch merge strategy: merge clips in groups to reduce iterations
    batch_size = 10  # merge 10 clips at a time
    temp_batch_files = []
    batch_index = 0
    
    # first pass: merge clips in batches
    for i in range(0, len(processed_clips), batch_size):
        batch = processed_clips[i:i + batch_size]
        if len(batch) == 1:
            # if only one clip in batch, just use it
            temp_batch_files.append(batch[0].file_path)
            continue
            
        batch_index += 1
        logger.info(f"merging batch {batch_index}, clips {i+1} to {min(i+batch_size, len(processed_clips))}")
        
        try:
            # load all clips in this batch
            clips_to_merge = []
            with suppress_moviepy_output():
                for clip_info in batch:
                    clip = VideoFileClip(clip_info.file_path)
                    clips_to_merge.append(clip)
            
            # merge all clips in this batch at once
            merged_batch = concatenate_videoclips(clips_to_merge)
            
            # save batch result
            batch_file = f"{output_dir}/temp-batch-{batch_index}.mp4"
            merged_batch.write_videofile(
                filename=batch_file,
                threads=threads,
                logger=None,
                temp_audiofile_path=output_dir,
                audio_codec=audio_codec,
                fps=fps,
            )
            
            # close all clips
            for clip in clips_to_merge:
                close_clip(clip)
            close_clip(merged_batch)
            
            temp_batch_files.append(batch_file)
            
        except Exception as e:
            logger.error(f"failed to merge batch {batch_index}: {str(e)}")
            # if batch merge fails, add individual files
            for clip_info in batch:
                temp_batch_files.append(clip_info.file_path)
    
    # if we have multiple batches, merge them recursively
    while len(temp_batch_files) > 1:
        logger.info(f"merging {len(temp_batch_files)} batch files")
        next_level_files = []
        
        for i in range(0, len(temp_batch_files), batch_size):
            batch = temp_batch_files[i:i + batch_size]
            if len(batch) == 1:
                next_level_files.append(batch[0])
                continue
                
            try:
                clips_to_merge = []
                with suppress_moviepy_output():
                    for file_path in batch:
                        clip = VideoFileClip(file_path)
                        clips_to_merge.append(clip)
                
                merged_clip = concatenate_videoclips(clips_to_merge)
                
                batch_index += 1
                batch_file = f"{output_dir}/temp-batch-{batch_index}.mp4"
                merged_clip.write_videofile(
                    filename=batch_file,
                    threads=threads,
                    logger=None,
                    temp_audiofile_path=output_dir,
                    audio_codec=audio_codec,
                    fps=fps,
                )
                
                for clip in clips_to_merge:
                    close_clip(clip)
                close_clip(merged_clip)
                
                next_level_files.append(batch_file)
                
                # delete intermediate files
                for file_path in batch:
                    if file_path.startswith(f"{output_dir}/temp-batch-"):
                        delete_files(file_path)
                        
            except Exception as e:
                logger.error(f"failed to merge batch files: {str(e)}")
                next_level_files.extend(batch)
        
        temp_batch_files = next_level_files
    
    # move final result to target location
    if temp_batch_files:
        shutil.move(temp_batch_files[0], combined_video_path)
    
    # clean temp files
    clip_files = [clip.file_path for clip in processed_clips]
    delete_files(clip_files)
            
    logger.info("video combining completed")
    return combined_video_path


def wrap_text(text, max_width, font="Arial", fontsize=60):
    # Create ImageFont
    font = ImageFont.truetype(font, fontsize)

    def get_text_size(inner_text):
        inner_text = inner_text.strip()
        left, top, right, bottom = font.getbbox(inner_text)
        return right - left, bottom - top

    width, height = get_text_size(text)
    if width <= max_width:
        return text, height

    processed = True

    _wrapped_lines_ = []
    words = text.split(" ")
    _txt_ = ""
    for word in words:
        _before = _txt_
        _txt_ += f"{word} "
        _width, _height = get_text_size(_txt_)
        if _width <= max_width:
            continue
        else:
            if _txt_.strip() == word.strip():
                processed = False
                break
            _wrapped_lines_.append(_before)
            _txt_ = f"{word} "
    _wrapped_lines_.append(_txt_)
    if processed:
        _wrapped_lines_ = [line.strip() for line in _wrapped_lines_]
        result = "\n".join(_wrapped_lines_).strip()
        height = len(_wrapped_lines_) * height
        return result, height

    _wrapped_lines_ = []
    chars = list(text)
    _txt_ = ""
    for word in chars:
        _txt_ += word
        _width, _height = get_text_size(_txt_)
        if _width <= max_width:
            continue
        else:
            _wrapped_lines_.append(_txt_)
            _txt_ = ""
    _wrapped_lines_.append(_txt_)
    result = "\n".join(_wrapped_lines_).strip()
    height = len(_wrapped_lines_) * height
    return result, height


def generate_video(
    video_path: str,
    audio_path: str,
    subtitle_path: str,
    output_file: str,
    params: VideoParams,
):
    aspect = VideoAspect(params.video_aspect)
    video_width, video_height = aspect.to_resolution()

    logger.info(f"generating video: {video_width} x {video_height}")
    logger.info(f"  ① video: {video_path}")
    logger.info(f"  ② audio: {audio_path}")
    logger.info(f"  ③ subtitle: {subtitle_path}")
    logger.info(f"  ④ output: {output_file}")

    # https://github.com/harry0703/MoneyPrinterTurbo/issues/217
    # PermissionError: [WinError 32] The process cannot access the file because it is being used by another process: 'final-1.mp4.tempTEMP_MPY_wvf_snd.mp3'
    # write into the same directory as the output file
    output_dir = os.path.dirname(output_file)

    font_path = ""
    if params.subtitle_enabled:
        if not params.font_name:
            params.font_name = "STHeitiMedium.ttc"
        font_path = os.path.join(utils.font_dir(), params.font_name)
        if os.name == "nt":
            font_path = font_path.replace("\\", "/")

        logger.info(f"  ⑤ font: {font_path}")

    def create_text_clip(subtitle_item):
        params.font_size = int(params.font_size)
        params.stroke_width = int(params.stroke_width)
        phrase = subtitle_item[1]
        max_width = video_width * 0.9
        wrapped_txt, txt_height = wrap_text(
            phrase, max_width=max_width, font=font_path, fontsize=params.font_size
        )
        interline = int(params.font_size * 0.25)
        size=(int(max_width), int(txt_height + params.font_size * 0.25 + (interline * (wrapped_txt.count("\n") + 1))))

        _clip = TextClip(
            text=wrapped_txt,
            font=font_path,
            font_size=params.font_size,
            color=params.text_fore_color,
            bg_color=params.text_background_color,
            stroke_color=params.stroke_color,
            stroke_width=params.stroke_width,
            interline=interline,
            size=size,
        )
        duration = subtitle_item[0][1] - subtitle_item[0][0]
        _clip = _clip.with_start(subtitle_item[0][0])
        _clip = _clip.with_end(subtitle_item[0][1])
        _clip = _clip.with_duration(duration)
        if params.subtitle_position == "bottom":
            _clip = _clip.with_position(("center", video_height * 0.95 - _clip.h))
        elif params.subtitle_position == "top":
            _clip = _clip.with_position(("center", video_height * 0.05))
        elif params.subtitle_position == "custom":
            # Ensure the subtitle is fully within the screen bounds
            margin = 10  # Additional margin, in pixels
            max_y = video_height - _clip.h - margin
            min_y = margin
            custom_y = (video_height - _clip.h) * (params.custom_position / 100)
            custom_y = max(
                min_y, min(custom_y, max_y)
            )  # Constrain the y value within the valid range
            _clip = _clip.with_position(("center", custom_y))
        else:  # center
            _clip = _clip.with_position(("center", "center"))
        return _clip

    with suppress_moviepy_output():
        video_clip = VideoFileClip(video_path).without_audio()
        audio_clip = AudioFileClip(audio_path).with_effects(
            [afx.MultiplyVolume(params.voice_volume)]
        )

    def make_textclip(text):
        return TextClip(
            text=text,
            font=font_path,
            font_size=params.font_size,
        )

    if subtitle_path and os.path.exists(subtitle_path):
        sub = SubtitlesClip(
            subtitles=subtitle_path, encoding="utf-8", make_textclip=make_textclip
        )
        text_clips = []
        for item in sub.subtitles:
            clip = create_text_clip(subtitle_item=item)
            text_clips.append(clip)
        video_clip = CompositeVideoClip([video_clip, *text_clips])

    bgm_file = get_bgm_file(bgm_type=params.bgm_type, bgm_file=params.bgm_file)
    if bgm_file:
        try:
            with suppress_moviepy_output():
                bgm_clip = AudioFileClip(bgm_file).with_effects(
                    [
                        afx.MultiplyVolume(params.bgm_volume),
                        afx.AudioFadeOut(3),
                        afx.AudioLoop(duration=video_clip.duration),
                    ]
                )
            audio_clip = CompositeAudioClip([audio_clip, bgm_clip])
        except Exception as e:
            logger.error(f"failed to add bgm: {str(e)}")

    video_clip = video_clip.with_audio(audio_clip)
    video_clip.write_videofile(
        output_file,
        audio_codec=audio_codec,
        temp_audiofile_path=output_dir,
        threads=params.n_threads or 2,
        logger=None,
        fps=fps,
    )
    video_clip.close()
    del video_clip


def preprocess_video(materials: List[MaterialInfo], clip_duration=4):
    for material in materials:
        if not material.url:
            continue

        ext = utils.parse_extension(material.url)
        try:
            with suppress_moviepy_output():
                clip = VideoFileClip(material.url)
        except Exception:
            with suppress_moviepy_output():
                clip = ImageClip(material.url)

        width = clip.size[0]
        height = clip.size[1]
        if width < 480 or height < 480:
            logger.warning(f"low resolution material: {width}x{height}, minimum 480x480 required")
            continue

        if ext in const.FILE_TYPE_IMAGES:
            logger.info(f"processing image: {material.url}")
            # Create an image clip and set its duration to 3 seconds
            clip = (
                ImageClip(material.url)
                .with_duration(clip_duration)
                .with_position("center")
            )
            # Apply a zoom effect using the resize method.
            # A lambda function is used to make the zoom effect dynamic over time.
            # The zoom effect starts from the original size and gradually scales up to 120%.
            # t represents the current time, and clip.duration is the total duration of the clip (3 seconds).
            # Note: 1 represents 100% size, so 1.2 represents 120% size.
            zoom_clip = clip.resized(
                lambda t: 1 + (clip_duration * 0.03) * (t / clip.duration)
            )

            # Optionally, create a composite video clip containing the zoomed clip.
            # This is useful when you want to add other elements to the video.
            final_clip = CompositeVideoClip([zoom_clip])

            # Output the video to a file.
            video_file = f"{material.url}.mp4"
            final_clip.write_videofile(video_file, fps=30, logger=None)
            close_clip(clip)
            material.url = video_file
            logger.success(f"image processed: {video_file}")
    return materials

def get_local_videos():
    """
    获取指定路径下的所有视频文件并返回 MaterialInfo 列表
    
    Returns:
        List[MaterialInfo]: 视频素材信息列表，与 preprocess_video 方法返回格式一致
    """
    from app.config import config
    
    materials = []
    video_folder = config.app.get("material_directory", "")
    
    # 如果配置为空，使用默认路径
    if not video_folder:
        # 获取项目根目录
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        video_folder = os.path.join(project_root, "storage", "local_videos")
        logger.warning(f"配置文件中 material_directory 为空，使用默认路径: {video_folder}")
    else:
        # 处理相对路径
        if not os.path.isabs(video_folder):
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            video_folder = os.path.join(project_root, video_folder)
        
        # 处理环境变量
        video_folder = os.path.expandvars(video_folder)
        # 处理用户目录符号 ~
        video_folder = os.path.expanduser(video_folder)
    
    # 标准化路径
    video_folder = os.path.normpath(video_folder)
    
    logger.info(f"开始扫描本地视频文件夹: {video_folder}")
    
    # 检查文件夹是否存在
    if not os.path.exists(video_folder):
        logger.warning(f"指定的视频文件夹不存在: {video_folder}")
        return materials
    
    # 扫描支持的视频文件格式
    video_extensions = const.FILE_TYPE_VIDEOS  # ["mp4", "mov", "mkv", "webm"]
    image_extensions = const.FILE_TYPE_IMAGES  # ["jpg", "jpeg", "png", "bmp"]
    
    try:
        # 获取所有文件
        all_files = []
        for filename in os.listdir(video_folder):
            file_path = os.path.join(video_folder, filename)
            
            # 跳过目录
            if os.path.isdir(file_path):
                continue
                
            # 获取文件扩展名
            ext = utils.parse_extension(file_path)
            
            # 检查是否是支持的文件格式
            if ext.lower() in video_extensions or ext.lower() in image_extensions:
                all_files.append(file_path)
        
        logger.info(f"找到 {len(all_files)} 个媒体文件")
        
        # 随机选择最多100个文件
        if len(all_files) > 100:
            selected_files = random.sample(all_files, 100)
            logger.info(f"随机选择了 100 个文件进行处理")
        else:
            selected_files = all_files
            logger.info(f"使用所有 {len(selected_files)} 个文件")
        
        # 处理选中的文件
        for file_path in selected_files:
            filename = os.path.basename(file_path)
            ext = utils.parse_extension(file_path)
            
            # 创建 MaterialInfo 对象
            material_info = MaterialInfo()
            material_info.provider = "local"
            material_info.url = file_path
            
            # 简化验证，只设置基本信息
            if ext.lower() in video_extensions:
                material_info.duration = 10  # 默认时长
            else:  # 图片文件
                material_info.duration = 4  # 图片默认时长4秒
                
            materials.append(material_info)
            logger.debug(f"添加素材: {filename}")
    
    except Exception as e:
        logger.error(f"扫描视频文件夹时出错: {str(e)}")
        return []
    
    logger.info(f"共找到 {len(materials)} 个有效的视频素材")
    
    # 如果有图片文件，需要预处理转换为视频
    if materials:
        logger.info("开始预处理素材文件...")
        materials = preprocess_video(materials, clip_duration=4)
    
    return materials