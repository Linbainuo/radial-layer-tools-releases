# Radial Layer Tools

Substance 3D Painter 的开源图层轮盘插件。按住快捷键呼出径向菜单，滑向命令后松开即可执行常用图层、遮罩、调整和滤镜操作。

Open-source radial layer menu for Substance 3D Painter. Hold the shortcut, move toward a command, and release to run common layer, mask, adjustment, and filter actions.

> 本项目由 OpenAI Codex 在维护者林白糯的指导与审核下协助开发、整理并发布。
>
> This project is developed, prepared, and published with assistance from OpenAI Codex under the direction and review of maintainer Linbainuo.

## 功能 / Features

- 可配置的径向菜单与多套菜单预设
- 填充图层、画笔图层、绘图效果、填充效果、黑色遮罩、对比遮罩、颜色选择、锚定点和色阶命令
- 可直接添加空滤镜，再在 Painter 中选择具体滤镜资源
- Painter 滤镜目录、双语搜索与本地图标
- 自定义轮盘快捷键和命令快捷键
- 图层导航、显示/隐藏和菜单编辑
- 中文、英文以及跟随 Painter 语言
- 在插件设置页异步检查、下载并安装 GitHub Releases 更新
- Configurable radial menus and menu presets
- Layer, paint/fill effect, compare mask, anchor point, adjustment, filter, and shortcut commands
- Chinese/English command search and Painter-language following

## 安装 / Installation

1. 关闭 Substance 3D Painter。
2. 下载仓库，或从 Releases 下载发布包。
3. 将 `radial_layer_tools` 文件夹复制到：

   ```text
   Documents/Adobe/Adobe Substance 3D Painter/python/plugins/
   ```

4. 启动 Painter，在 `Python > Plugins` 中启用 `radial_layer_tools`。

Close Painter, copy the `radial_layer_tools` directory to Painter's Python plugin directory, restart Painter, and enable the plugin from `Python > Plugins`.

## 使用 / Usage

- 默认轮盘快捷键：反引号键 `` ` ``。
- 按住快捷键呼出轮盘，移动到扇区后松开执行。
- 文本输入控件获得焦点时，插件不会拦截轮盘快捷键。
- 插件设置、菜单和快捷键保存在本机生成的 `radial_layer_tools_config.json` 中。
- 更新或重新安装时不要删除该配置文件。

The default radial shortcut is the backtick key. User settings are stored locally in `radial_layer_tools_config.json`; this file is intentionally excluded from the repository and release packages.

## 更新 / Updates

- 更新检查使用公开的 GitHub Releases，不会上传 Painter 项目或插件配置。
- 可在“轮盘属性”中关闭自动检查，也可以手动检查。
- 安装前会验证 GitHub 提供的 SHA-256 摘要，并备份当前插件程序文件。
- 更新不会覆盖 `radial_layer_tools_config.json`，安装完成后需要重启 Painter。

Update checks use public GitHub Releases. Packages are verified before installation, the existing plugin code is backed up, and local radial menu configuration is preserved. Restart Painter after an update is installed.

## 项目结构 / Repository Layout

```text
radial_layer_tools/
  __init__.py
  icons/
.github/workflows/release.yml
scripts/package_release.py
CHANGELOG.md
LICENSE
THIRD_PARTY_NOTICES.md
```

## 开发 / Development

The live Painter configuration is not part of this repository. Keep generated configuration, caches, local captures, archived prototypes, and third-party reference projects out of commits.

Release archives are generated only when a matching `v*` Git tag is pushed. The tag version must match `PLUGIN_VERSION`; normal commits and pull requests never publish a release.

## 许可 / License

Project code is released under the [MIT License](LICENSE). Third-party names, trademarks, and visual assets are addressed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This is an independent community project and is not affiliated with or endorsed by Adobe.
