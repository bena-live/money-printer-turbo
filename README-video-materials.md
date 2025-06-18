# 本地视频素材配置说明

## 配置方法

在 `config.toml` 文件中设置 `material_directory` 参数来指定本地视频素材文件夹。

### 1. 直接配置路径

#### Windows:
```toml
material_directory = "F:/bena_pro/test/nodeOtherFolder/azure_modal/money_sucai/"
```

#### Linux/Mac:
```toml
material_directory = "/home/user/videos/materials/"
```

#### 相对路径:
```toml
material_directory = "./storage/local_videos/"
```

### 2. 使用环境变量

如果需要在不同环境中使用不同的路径，可以设置环境变量。

#### 设置环境变量

**Windows (PowerShell):**
```powershell
$env:VIDEO_MATERIALS_PATH = "F:/bena_pro/test/nodeOtherFolder/azure_modal/money_sucai/"
```

**Windows (CMD):**
```cmd
set VIDEO_MATERIALS_PATH=F:/bena_pro/test/nodeOtherFolder/azure_modal/money_sucai/
```

**Linux/Mac:**
```bash
export VIDEO_MATERIALS_PATH="/home/user/videos/materials/"
```

#### 在配置文件中使用环境变量:
```toml
material_directory = "$VIDEO_MATERIALS_PATH"
```

### 3. 使用用户目录

```toml
# 使用用户主目录
material_directory = "~/Videos/materials/"
```

## 支持的文件格式

### 视频文件:
- `.mp4`
- `.mov` 
- `.mkv`
- `.webm`

### 图片文件:
- `.jpg` / `.jpeg`
- `.png`
- `.bmp`

**注意**: 图片文件会自动转换为4秒的视频，并添加缩放动画效果。

## 文件要求

- 最小分辨率: 480x480
- 文件必须能被 MoviePy 正确读取

## 默认行为

如果 `material_directory` 为空或未设置：
- 将使用项目目录下的 `./storage/local_videos/` 作为默认路径
- 系统会自动创建此目录（如果不存在）

## 使用示例

1. 将视频文件放入配置的文件夹中
2. 在 WebUI 中选择"本地文件"作为视频来源
3. 系统会自动扫描并使用该文件夹中的所有有效视频文件

## 注意事项

- 文件夹路径支持绝对路径和相对路径
- 相对路径是相对于项目根目录
- 确保应用程序有读取指定文件夹的权限
- 大文件可能会影响视频生成速度