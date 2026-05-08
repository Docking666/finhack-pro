# FinHack Pro 图标资源说明

本目录包含应用程序所需的图标文件。请按照以下规范准备图标：

## 图标文件列表

| 文件名 | 用途 | 格式要求 | 推荐尺寸 |
|--------|------|----------|----------|
| `icon.ico` | Windows 应用图标 | ICO 格式，包含多种尺寸 | 256x256, 128x128, 64x64, 48x48, 32x32, 16x16 |
| `icon.png` | macOS/Linux 应用图标 | PNG 格式，透明背景 | 512x512 或 1024x1024 |
| `tray.png` | 系统托盘图标 | PNG 格式，透明背景 | 16x16 或 32x32 |

## 图标设计规范

### 1. 应用图标 (icon.ico / icon.png)

- **风格**: 现代、简洁、专业
- **颜色**: 建议使用品牌主色调 (#4F46E5 紫蓝色)
- **内容**: 建议包含金融/图表相关元素
- **背景**: 透明或圆角矩形背景

### 2. 托盘图标 (tray.png)

- **风格**: 简洁、易识别
- **尺寸**: 
  - Windows: 16x16 或 32x32
  - macOS: 16x16 (模板图标)
  - Linux: 22x22 或 24x24
- **颜色**: 
  - Windows/Linux: 单色或双色
  - macOS: 黑色模板图标（系统自动调整颜色）

## 图标生成工具推荐

### 在线工具
- [IconGenerator](https://icongenerator.net/) - 生成各平台图标
- [RealFaviconGenerator](https://realfavicongenerator.net/) - 网站图标生成
- [ConvertICO](https://convertico.com/) - PNG 转 ICO

### 本地工具
- **ImageMagick**: 命令行图标转换
  ```bash
  # 生成 ICO 文件
  convert icon-256.png -define icon:auto-resize=256,128,64,48,32,16 icon.ico
  ```
- **GIMP**: 图像编辑和导出
- **Inkscape**: SVG 矢量图编辑

## 图标制作示例

### 使用 ImageMagick 批量生成

```bash
# 从大图生成各尺寸 PNG
convert icon-large.png -resize 512x512 icon-512.png
convert icon-large.png -resize 256x256 icon-256.png
convert icon-large.png -resize 128x128 icon-128.png
convert icon-large.png -resize 64x64 icon-64.png
convert icon-large.png -resize 32x32 icon-32.png
convert icon-large.png -resize 16x16 icon-16.png

# 生成 ICO 文件
convert icon-256.png icon-128.png icon-64.png icon-32.png icon-16.png icon.ico
```

### 使用 Python 生成

```python
from PIL import Image

# 打开原始图标
icon = Image.open('icon-large.png')

# 生成不同尺寸
sizes = [512, 256, 128, 64, 48, 32, 16]
for size in sizes:
    resized = icon.resize((size, size), Image.LANCZOS)
    resized.save(f'icon-{size}.png')

# 生成 ICO 文件
icon.save('icon.ico', format='ICO', sizes=[(s, s) for s in [256, 128, 64, 48, 32, 16]])
```

## 临时占位图标

在正式图标准备好之前，可以使用以下方法生成临时图标：

```bash
# 创建简单的占位图标
convert -size 256x256 xc:#4F46E5 -gravity center -pointsize 72 -fill white -annotate 0 "FH" icon.png
convert icon.png -define icon:auto-resize=256,128,64,48,32,16 icon.ico
convert -size 32x32 xc:#4F46E5 -gravity center -pointsize 16 -fill white -annotate 0 "FH" tray.png
```

## 注意事项

1. **版权**: 确保图标不侵犯第三方版权
2. **一致性**: 各平台图标风格应保持一致
3. **可识别性**: 小尺寸图标应保持清晰可辨
4. **透明度**: 正确处理透明背景
5. **测试**: 在不同背景色下测试图标显示效果

## 文件结构

```
assets/
├── icon.ico          # Windows 应用图标
├── icon.png          # macOS/Linux 应用图标
├── tray.png          # 系统托盘图标
└── README.md         # 本说明文件
```

## 打包注意事项

- 打包时会自动包含 `assets` 目录下的所有文件
- 确保 ICO 文件包含多种尺寸以适应不同场景
- macOS 图标建议使用 1024x1024 高清版本
