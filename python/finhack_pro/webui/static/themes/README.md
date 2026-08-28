# 主题文件规范

界面配色完全由主题文件驱动，CSS 里不硬编码任何颜色。内置主题（`mono-dark` / `mono-light`）
与第三方主题走同一条链路，因此制作一个新主题不需要改动任何代码。

## 快速开始

1. 复制 `mono-dark.json` 改名（例如 `my-theme.json`）
2. 改 `id`、`name` 和 `tokens` 里的色值
3. 把文件放进 **`data/themes/`**（运行时目录，不进 Git）
4. 刷新页面 →「API配置 → 界面设置 → 主题」下拉中即可选择

也可以通过 API 管理：`GET/POST/PUT/DELETE /api/themes`。

## 文件结构

```jsonc
{
  "id": "my-theme",          // 必需。小写字母/数字/-/_，最长 40 字符，作文件名用
  "name": "我的主题",         // 显示名
  "version": "1.0",
  "author": "your-name",
  "type": "user",            // user | third-party（不可填 builtin，内置主题只读）
  "mode": "dark",            // dark | light，决定明暗基调
  "scheme": "cn",            // cn=涨红跌绿（A股）| us=涨绿跌红（欧美）
  "wallpaper": null,         // 见下方「壁纸」
  "tokens": { /* 见下方「Token」*/ },
  "customCss": null          // 见下方「自定义 CSS」
}
```

## 色值格式

支持三种写法：

| 写法 | 示例 | 适用 |
|---|---|---|
| 3 位 hex | `#abc` | 实色 |
| 6 位 hex | `#0B0C0E` | 实色（推荐） |
| rgba() | `rgba(232,234,237,.10)` | 半透明（soft 类） |

实色会被自动转成 `R G B` 分量格式注入 CSS，这样才能支持 Tailwind 的透明度修饰符
（如 `bg-surface/50`）。所以**实色请写 hex，不要自己写分量值**。

## Token

### 必需（缺失会自动用内置默认值补齐）

| 分类 | Token |
|---|---|
| 背景 | `bg-base`（页面底）、`bg-surface`（卡片/面板）、`bg-elevated`（hover/次级按钮）、`bg-inset`（输入框/代码块） |
| 分隔线 | `line`（发丝线，模块边界）、`line-strong`（hover/焦点）、`line-subtle`（表格行内弱分隔） |
| 文字 | `fg`（主）、`fg-muted`（次要）、`fg-subtle`（标签/提示） |
| 强调 | `accent`（主强调：暗=白 / 白=黑）、`accent-fg`（accent 上的文字反色） |
| 涨跌 | `up`（涨）、`down`（跌） |
| 状态 | `ok`（成功）、`warn`（警告）、`danger`（失败） |

### 可选

| Token | 用途 |
|---|---|
| `accent-soft`、`up-soft`、`down-soft` | 对应色的半透明底（徽章用），需写 rgba() |
| `chart-line`、`chart-line2` | 图表主/次线条 |
| `chart-grid`、`chart-axis` | 网格线、坐标轴文字 |
| `chart-tip-bg`、`chart-tip-line` | Tooltip 背景与边框 |
| `chart-c1` ~ `chart-c8` | 图表系列色与 Agent 身份色（低饱和，避免彩虹感） |

## 壁纸

```jsonc
"wallpaper": {
  "url": "/themes/wallpapers/my-bg.jpg",  // 上传后得到的路径
  "mode": "cover",      // cover | contain | tile
  "blur": 0,            // 模糊像素
  "overlay": 0.5,       // 底色遮罩 0~1，建议 ≥0.4 以保证文字可读
  "position": "center"
}
```

不需要壁纸时设为 `null`，界面保持纯色。壁纸通过 `POST /api/themes/wallpaper` 上传
（jpg/png/webp，≤5MB），存于 `data/themes/wallpapers/`。

## 自定义 CSS

`customCss` 字段可填任意 CSS 字符串，会在应用主题时注入页面。注意：

- **只对非内置主题生效**（内置主题保持纯净）
- 这是给自己和可信来源用的逃生舱，效果等同于本地 CSS 注入
- 不希望任何主题携带自定义样式时，可在界面设置里关闭

## 涨跌配色

`up` / `down` 的**语义是"涨"和"跌"，不是"红"和"绿"**。具体红绿由用户在界面设置里
选择的方向决定，主题文件里的 `scheme` 只是该主题的推荐默认值。

所以做主题时：涨色填 `up`，跌色填 `down`——至于涨该是红还是绿，交给使用者决定。

状态色（`ok`/`warn`/`danger`）与涨跌色刻意使用不同色相，二者同屏时可区分，
制作主题时请保持这个区分（例如不要给 `ok` 和 `up` 用同一个绿）。
