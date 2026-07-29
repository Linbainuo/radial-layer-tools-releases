# Radial Layer Tools

Substance 3D Painter 的开源图层轮盘插件。按住快捷键、滑向命令并松开，即可快速执行常用图层操作。

Open-source radial layer tools for Substance 3D Painter. Hold the shortcut, move toward a command, and release to run it.

> 本项目使用了 AI 辅助工具开发。
>
> AI-assisted tools were used during development.

## 功能 / Features

- 自定义轮盘、菜单预设和快捷键
- 图层、遮罩、特效、调整与 Painter 滤镜
- 中英文搜索，支持跟随 Painter 语言
- 在插件内检查、下载并安装更新

## 安装 / Installation

1. 从 [Releases](https://github.com/Linbainuo/radial-layer-tools-releases/releases/latest) 下载 ZIP。
2. 将其中的 `radial_layer_tools` 文件夹复制到：

   ```text
   Documents/Adobe/Adobe Substance 3D Painter/python/plugins/
   ```

3. 重启 Painter，在 `Python > Plugins` 中启用 `radial_layer_tools`。

Download the latest release, copy `radial_layer_tools` into Painter's Python plugin directory, restart Painter, and enable it from `Python > Plugins`.

## 使用 / Usage

- 默认轮盘快捷键：反引号键 `` ` ``。
- 按住呼出轮盘，移动到扇区后松开执行。
- 设置保存在本机 `radial_layer_tools_config.json` 中，更新不会覆盖。

## 更新 / Updates

插件通过公开的 GitHub Releases 检查更新。安装包会校验 SHA-256，并保留本地配置。

Updates use public GitHub Releases. Packages are verified before installation, and local settings are preserved.

## 许可 / License

[MIT License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

由林白糯维护。本项目为独立社区项目，与 Adobe 无隶属或认可关系。

Maintained by Linbainuo. This independent community project is not affiliated with or endorsed by Adobe.
