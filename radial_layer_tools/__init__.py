import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
import traceback
import ctypes
import ctypes.wintypes
import time
import zipfile

from PySide6 import QtCore, QtGui, QtNetwork, QtWidgets

import substance_painter as sp
import substance_painter.logging
import substance_painter.ui


CONFIG_FILE = "radial_layer_tools_config.json"
PLUGIN_VERSION = "1.0.2"
PLUGIN_BUILD = "2026.07.23-v1.0.2-release"
GITHUB_REPOSITORY = "Linbainuo/radial-layer-tools-releases"
GITHUB_REPOSITORY_URL = "https://github.com/" + GITHUB_REPOSITORY
GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPOSITORY)
UPDATE_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
UPDATE_REQUEST_TIMEOUT_MS = 15000


QML = r'''
import QtQuick 2.15

Item {
    id: root
    width: (wheelRadius + wheelMargin) * 2
    height: width

    property real centerX: width * 0.5
    property real centerY: height * 0.5
    property real segmentAngle: 360 / Math.max(1, tools.count)
    property int hoveredIndex: -1

    function setHoveredIndex(index) {
        if (hoveredIndex === index)
            return
        hoveredIndex = index
        wheel.requestPaint()
        backend.setHoveredAction(index >= 0 ? toolModel[index].action : "")
    }

    function updateHover(mouseX, mouseY) {
        var dx = mouseX - centerX
        var dy = mouseY - centerY
        var distance = Math.sqrt(dx * dx + dy * dy)
        if (distance < innerRadius || distance > wheelRadius) {
            setHoveredIndex(-1)
            return
        }
        var angle = Math.atan2(dy, dx) * 180 / Math.PI
        var firstBoundary = -90 - segmentAngle * 0.5
        var relative = (angle - firstBoundary + 360) % 360
        setHoveredIndex(Math.min(tools.count - 1, Math.floor(relative / segmentAngle)))
    }

    Canvas {
        id: wheel
        anchors.fill: parent
        antialiasing: true
        Component.onCompleted: requestPaint()

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)

            for (var shadow = 12; shadow >= 3; shadow -= 3) {
                ctx.beginPath()
                ctx.arc(root.centerX, root.centerY, wheelRadius + shadow * 0.35, 0, Math.PI * 2)
                ctx.strokeStyle = "rgba(0, 0, 0, " + (0.035 * (13 - shadow)) + ")"
                ctx.lineWidth = shadow
                ctx.stroke()
            }

            var count = Math.max(1, tools.count)
            var span = Math.PI * 2 / count
            var firstCenter = -Math.PI * 0.5
            for (var i = 0; i < count; ++i) {
                var center = firstCenter + span * i
                var start = center - span * 0.5
                var end = center + span * 0.5
                ctx.beginPath()
                ctx.arc(root.centerX, root.centerY, wheelRadius, start, end, false)
                ctx.arc(root.centerX, root.centerY, innerRadius, end, start, true)
                ctx.closePath()
                ctx.fillStyle = i === hoveredIndex ? "#333333" : "#262626"
                ctx.fill()
                ctx.strokeStyle = i === hoveredIndex ? "#5a5a5a" : "#454545"
                ctx.lineWidth = 1
                ctx.stroke()

                if (i === hoveredIndex) {
                    ctx.beginPath()
                    ctx.arc(root.centerX, root.centerY, wheelRadius - 3, start + 0.025, end - 0.025, false)
                    ctx.strokeStyle = highlightColor
                    ctx.lineWidth = 6
                    ctx.stroke()
                }
            }

            ctx.beginPath()
            ctx.arc(root.centerX, root.centerY, wheelRadius, 0, Math.PI * 2)
            ctx.strokeStyle = "#171717"
            ctx.lineWidth = 10
            ctx.stroke()

            ctx.beginPath()
            ctx.arc(root.centerX, root.centerY, innerRadius, 0, Math.PI * 2)
            ctx.fillStyle = "#222222"
            ctx.fill()
            ctx.strokeStyle = "#4a4a4a"
            ctx.lineWidth = 2
            ctx.stroke()
        }
    }

    Repeater {
        id: tools
        model: toolModel
        delegate: Item {
            property real angle: (-90 + root.segmentAngle * index) * Math.PI / 180
            property real iconRadius: (wheelRadius + innerRadius) * 0.5
            width: iconSize + 12
            height: width
            x: root.centerX + Math.cos(angle) * iconRadius - width * 0.5
            y: root.centerY + Math.sin(angle) * iconRadius - height * 0.5

            Image {
                id: commandIcon
                anchors.centerIn: parent
                width: iconSize
                height: iconSize
                sourceSize.width: iconSize
                sourceSize.height: iconSize
                fillMode: Image.PreserveAspectFit
                source: modelData.icon
                smooth: true
                opacity: root.hoveredIndex === index ? 1.0 : 0.58
            }

        }
    }

    Text {
        id: centerText
        anchors.centerIn: parent
        width: innerRadius * 1.55
        text: root.hoveredIndex >= 0 ? toolModel[root.hoveredIndex].label : centerLabel
        color: root.hoveredIndex >= 0 ? "#f2f2f2" : "#a6a6a6"
        font.pixelSize: 13
        font.bold: root.hoveredIndex >= 0
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onPositionChanged: root.updateHover(mouse.x, mouse.y)
        onExited: root.setHoveredIndex(-1)
    }
}
'''


DEFAULT_CONFIG = {
    "language": "painter",
    "shortcut": {"key": "`", "modifiers": []},
    "auto_check_updates": True,
    "command_shortcuts": [],
    "radius": 128,
    "dead_zone_radius": 42,
    "card_width": 136,
    "card_height": 32,
    "wheel_radius": 154,
    "inner_radius": 76,
    "wheel_margin": 22,
    "icon_size": 23,
    "highlight_color": "#2d8fe8",
    "items": [
        {
            "id": "fill_layer",
            "labels": {"en": "Fill Layer", "zh_CN": "填充图层"},
            "descriptions": {"en": "Create at stack top", "zh_CN": "在顶部新建"},
            "action": "add_fill_layer",
            "category": "layers",
            "icon": "style:views/icons/effects_fill.svg"
        },
        {
            "id": "paint_layer",
            "labels": {"en": "Paint Layer", "zh_CN": "画笔图层"},
            "descriptions": {
                "en": "Create a paint layer at stack top",
                "zh_CN": "在顶部新建画笔图层"
            },
            "action": "add_paint_layer",
            "category": "layers",
            "icon": "style:views/icons/effects_paint.svg"
        },
        {
            "id": "paint_effect",
            "labels": {"en": "Add Paint", "zh_CN": "添加绘图"},
            "descriptions": {
                "en": "Insert a paint effect at the current stack position",
                "zh_CN": "在当前堆栈位置添加绘图效果"
            },
            "action": "add_paint_effect",
            "category": "effects",
            "icon": "style:views/icons/effects_paint.svg"
        },
        {
            "id": "fill_effect",
            "labels": {"en": "Add Fill", "zh_CN": "添加填充"},
            "descriptions": {
                "en": "Insert a fill effect at the current stack position",
                "zh_CN": "在当前堆栈位置添加填充效果"
            },
            "action": "add_fill_effect",
            "category": "effects",
            "icon": "style:views/icons/effects_fill.svg"
        },
        {
            "id": "black_mask",
            "labels": {"en": "Black Mask", "zh_CN": "黑色遮罩"},
            "descriptions": {"en": "Add to selected layer", "zh_CN": "添加到当前图层"},
            "action": "add_black_mask",
            "category": "masks",
            "icon": "style:views/icons/thumbnail_add_mask.svg"
        },
        {
            "id": "compare_mask",
            "labels": {"en": "Add Compare Mask", "zh_CN": "添加对比遮罩"},
            "descriptions": {
                "en": "Insert a compare mask in the selected layer mask",
                "zh_CN": "在当前图层遮罩中添加对比遮罩"
            },
            "action": "add_compare_mask",
            "category": "masks",
            "icon": "style:views/icons/effects_comparemask.svg"
        },
        {
            "id": "filter_effect",
            "labels": {"en": "Add Filter", "zh_CN": "添加滤镜"},
            "descriptions": {
                "en": "Insert an empty filter effect",
                "zh_CN": "添加一个空滤镜效果"
            },
            "action": "add_filter_effect",
            "category": "filters",
            "icon": "style:views/icons/effects_substance.svg"
        },
        {
            "id": "anchor_point",
            "labels": {"en": "Add Anchor Point", "zh_CN": "添加锚定点"},
            "descriptions": {
                "en": "Insert an anchor point at the current stack position",
                "zh_CN": "在当前堆栈位置添加锚定点"
            },
            "action": "add_anchor_point",
            "category": "effects",
            "icon": "style:views/icons/effects_anchor.svg"
        },
        {
            "id": "blur",
            "labels": {"en": "Blur", "zh_CN": "模糊"},
            "descriptions": {"en": "Filter effect", "zh_CN": "滤镜效果"},
            "action": "add_blur_filter",
            "category": "filters",
            "icon": "style:views/icons/effects_substance.svg"
        },
        {
            "id": "levels",
            "labels": {"en": "Levels", "zh_CN": "色阶"},
            "descriptions": {"en": "Adjust active channel", "zh_CN": "调整当前通道"},
            "action": "add_levels",
            "category": "adjustments",
            "icon": "style:views/icons/effects_levels.svg"
        },
        {
            "id": "color_selection",
            "labels": {"en": "Color Selection", "zh_CN": "颜色选择"},
            "descriptions": {"en": "Add in mask stack", "zh_CN": "添加到遮罩下"},
            "action": "add_color_selection",
            "category": "masks",
            "icon": "style:views/icons/effects_colorselection.svg"
        }
    ]
}


OFFICIAL_ICONS = {
    "fill_layer": "style:views/icons/effects_fill.svg",
    "paint_layer": "style:views/icons/effects_paint.svg",
    "paint_effect": "style:views/icons/effects_paint.svg",
    "fill_effect": "style:views/icons/effects_fill.svg",
    "black_mask": "style:views/icons/thumbnail_add_mask.svg",
    "compare_mask": "style:views/icons/effects_comparemask.svg",
    "filter_effect": "style:views/icons/effects_substance.svg",
    "anchor_point": "style:views/icons/effects_anchor.svg",
    "blur": "style:views/icons/effects_substance.svg",
    "levels": "style:views/icons/effects_levels.svg",
    "color_selection": "style:views/icons/effects_colorselection.svg"
}


ICON_FILES = {
    "fill_layer": "fill_layer.png",
    "paint_layer": "paint_layer.png",
    "paint_effect": "paint_layer.png",
    "fill_effect": "fill_layer.png",
    "black_mask": "black_mask.png",
    "compare_mask": "compare_mask.png",
    "filter_effect": "blur.png",
    "anchor_point": "anchor_point.png",
    "blur": "blur.png",
    "levels": "levels.png",
    "color_selection": "color_selection.png",
    "edit": "edit.png",
    "delete": "delete.png",
    "settings": "gear.png",
    "back": "back.png",
    "reset": "reset.png",
    "github": "github.svg"
}


FILTER_ZH_LABELS = {
    "anisotropic kuwahara": "各向异性 Kuwahara",
    "baked lighting environment": "烘焙环境光照",
    "baked lighting stylized": "烘焙风格化光照",
    "bevel": "倒角",
    "bevel smooth": "平滑倒角",
    "blur": "模糊",
    "blur directional": "方向模糊",
    "blur slope": "斜坡模糊",
    "clamp": "钳制",
    "color balance": "色彩平衡",
    "color correct": "色彩校正",
    "color match": "色彩匹配",
    "colorize": "着色",
    "contrast luminosity": "亮度对比度",
    "directional distance": "方向距离",
    "drop shadow": "投影",
    "fill area color": "区域填充颜色",
    "fill area mask": "区域填充遮罩",
    "fxaa (anti-aliasing)": "FXAA（抗锯齿）",
    "glow": "发光",
    "gradient": "渐变",
    "gradient curve": "渐变曲线",
    "gradient dynamic": "动态渐变",
    "grayscale conversion": "灰度转换",
    "height adjust": "高度调整",
    "height to normal": "高度转法线",
    "highpass": "高通",
    "histogram scan": "直方图扫描",
    "histogram shift": "直方图偏移",
    "hsl perceptive": "感知 HSL",
    "invert": "反相",
    "mask outline": "遮罩轮廓",
    "matfinish brushed linear": "材质表面：线性拉丝",
    "matfinish galvanized": "材质表面：镀锌",
    "matfinish grainy": "材质表面：颗粒",
    "matfinish grinded": "材质表面：研磨",
    "matfinish hammered": "材质表面：锤纹",
    "matfinish perforated circles": "材质表面：圆孔",
    "matfinish powder coated": "材质表面：粉末涂层",
    "matfinish raw": "材质表面：原始",
    "matfinish rough": "材质表面：粗糙",
    "matfx comic book": "材质特效：漫画",
    "matfx detail edge wear": "材质特效：细节边缘磨损",
    "matfx edge damages": "材质特效：边缘损伤",
    "matfx hbao": "材质特效：HBAO",
    "matfx oil paint": "材质特效：油画",
    "matfx peeling paint": "材质特效：油漆剥落",
    "matfx rust weathering": "材质特效：锈蚀风化",
    "matfx shut line": "材质特效：接缝线",
    "matfx water drops": "材质特效：水滴",
    "matfx watercolor": "材质特效：水彩",
    "mirror": "镜像",
    "painter_colorize": "Painter 着色",
    "pbr validate (metallic roughness)": "PBR 验证（金属度/粗糙度）",
    "pixelate": "像素化",
    "posterize": "色调分离",
    "quantize": "量化",
    "sharpen": "锐化",
    "smoothstep": "平滑阶梯",
    "stylization": "风格化",
    "threshold": "阈值",
    "transform": "变换",
    "tri-planar advanced": "高级三平面投射",
    "warp": "扭曲"
}


LEGACY_ICONS = {
    "qrc:/ImagesGUI/path_fill.svg",
    "qrc:/ImagesGUI/check_off.svg",
    "qrc:/ImagesGUI/substance_logo.svg",
    "qrc:/ImagesGUI/icon_color_channel.svg",
    "qrc:/ImagesGUI/blank_32.svg"
}


FILTER_ACTION_PREFIX = "add_filter_resource:"
COMMAND_CATEGORY_ORDER = (
    "layers", "masks", "effects", "adjustments", "filters", "other")
COMMAND_CATEGORY_KEYS = {
    "layers": "category_layers",
    "masks": "category_masks",
    "effects": "category_effects",
    "adjustments": "category_adjustments",
    "filters": "category_filters",
    "other": "category_other"
}
COMMAND_CATEGORY_BY_ID = {
    "fill_layer": "layers",
    "paint_layer": "layers",
    "paint_effect": "effects",
    "fill_effect": "effects",
    "black_mask": "masks",
    "compare_mask": "masks",
    "color_selection": "masks",
    "anchor_point": "effects",
    "levels": "adjustments",
    "filter_effect": "filters",
    "blur": "filters"
}
ROLE_ITEM_ID = QtCore.Qt.UserRole
ROLE_ITEM_ENABLED = QtCore.Qt.UserRole + 1
ROLE_ROW_KIND = QtCore.Qt.UserRole + 2
ROLE_CATEGORY = QtCore.Qt.UserRole + 3
ROW_COMMAND = "command"
ROW_CATEGORY = "category"


FALLBACK_GLYPHS = {
    "fill_layer": "+",
    "paint_layer": "P",
    "paint_effect": "P",
    "fill_effect": "F",
    "black_mask": "M",
    "compare_mask": "C",
    "filter_effect": "S",
    "anchor_point": "A",
    "blur": "B",
    "levels": "L",
    "color_selection": "C"
}


TRANSLATIONS = {
    "en": {
        "dialog_title": "Radial Layer Tools",
        "title": "Layer Tools",
        "subtitle": "Press the shortcut, then choose a card",
        "open_settings": "Radial Layer Tools Settings...",
        "settings_title": "Radial Menu Editor",
        "search_commands": "Search commands...",
        "commands": "Menu Commands",
        "tab_commands": "Commands",
        "tab_combinations": "Menus",
        "tab_shortcuts": "Shortcuts",
        "shortcut_empty": "Double-click a command on the left to add a shortcut",
        "remove_shortcut": "Remove shortcut",
        "default_menu": "Default Menu",
        "new_menu": "New menu",
        "new_menu_name": "Menu {number}",
        "menu_item_count": "{count} items",
        "rename_menu": "Rename menu",
        "delete_menu": "Delete menu",
        "menu_name_prompt": "Menu name",
        "delete_menu_confirm": "Delete \"{name}\"?",
        "appearance": "Appearance",
        "wheel_properties": "Wheel properties",
        "open_properties": "Open wheel properties",
        "back_to_commands": "Back to commands",
        "shortcut": "Shortcut",
        "press_key": "Press a key",
        "reset_shortcut": "Restore default shortcut",
        "reset_wheel_radius": "Restore default wheel radius",
        "reset_inner_radius": "Restore default center radius",
        "wheel_radius": "Wheel radius",
        "inner_radius": "Center radius",
        "language": "Language",
        "language_auto": "Automatic",
        "language_painter": "Follow Painter",
        "language_zh": "Chinese",
        "language_en": "English",
        "move_up": "Move up",
        "move_down": "Move down",
        "apply": "Apply",
        "applied": "Applied",
        "reset": "Reset",
        "reset_confirm_title": "Restore defaults",
        "reset_confirm_message": (
            "Restore all radial menu settings to their defaults?\n\n"
            "This replaces the current shortcut, appearance, menus, and command layout "
            "in the editor. Nothing is saved until you click Apply."
        ),
        "close": "Cancel",
        "highlight_color": "Highlight",
        "selected_status": "Selected: {name}",
        "editor_hint": "Drag a wheel segment to reorder it. Drag outside the wheel to remove it.",
        "move_to_position": "Move to position {position}",
        "release_to_remove": "Release to remove",
        "restore_command": "Double-click to restore to the wheel",
        "category_layers": "Layers",
        "category_masks": "Masks",
        "category_effects": "Effects",
        "category_adjustments": "Adjustments",
        "category_filters": "Filters",
        "category_other": "Other",
        "save_failed": "Could not save the radial menu configuration.",
        "node_fill": "Radial Fill Layer",
        "node_blur": "Radial Blur",
        "node_levels": "Radial Levels",
        "black_mask_exists": "The selected layer already has a mask.",
        "anchor_point_name": "Anchor Point",
        "no_active_stack": "No active texture set stack. Open a Painter project first.",
        "blur_missing": "Could not find a Blur filter resource in the Painter shelves.",
        "updates": "Updates",
        "open_repository": "Open GitHub repository",
        "auto_check_updates": "Check automatically",
        "check_updates": "Check for updates",
        "checking_updates": "Checking...",
        "up_to_date": "V{version} is up to date",
        "no_releases": "No public release is available yet",
        "update_available": "V{version} is available",
        "download_update": "Download and install",
        "downloading_update": "Downloading... {progress}%",
        "installing_update": "Installing...",
        "restart_to_finish": "Installed. Restart Painter to finish.",
        "restart_painter": "Restart Painter",
        "restart_prompt_title": "Restart Painter",
        "restart_prompt_message": (
            "V{version} was installed successfully. Restart Painter now to load "
            "the update?\n\nIf the current project has unsaved changes, Painter "
            "will ask whether to save them."
        ),
        "restart_later": "Restart later",
        "restart_now": "Restart now",
        "update_failed": "Update failed: {detail}",
        "update_confirm_title": "Install update",
        "update_confirm_message": (
            "Download and install V{version}?\n\n"
            "Your radial menu configuration will be preserved. Restart Painter "
            "after installation to load the new version."
        ),
        "confirm_install": "Install",
        "invalid_version": "The release version is invalid",
        "release_missing_zip": "The release has no plugin ZIP package",
        "checksum_missing": "The release package has no SHA-256 digest",
        "checksum_mismatch": "The downloaded package failed verification",
        "unsafe_update_redirect": "The update redirected to an untrusted address",
        "too_many_update_redirects": "The update redirected too many times",
        "timeout": "The GitHub request timed out",
        "invalid_update": "The update package is invalid",
        "version_mismatch": "The package version does not match the release",
        "update_too_large": "The update package is too large",
        "unsafe_update_path": "The update package contains an unsafe path",
        "unsafe_update_link": "The update package contains an unsafe link",
        "network_error": "Could not connect to GitHub"
    },
    "zh_CN": {
        "dialog_title": "图层轮盘",
        "title": "图层工具",
        "subtitle": "按住快捷键打开，松开执行悬停项",
        "open_settings": "图层轮盘设置...",
        "settings_title": "径向菜单编辑器",
        "search_commands": "搜索命令...",
        "commands": "菜单命令",
        "tab_commands": "命令",
        "tab_combinations": "菜单",
        "tab_shortcuts": "快捷键",
        "shortcut_empty": "双击左侧命令添加快捷键",
        "remove_shortcut": "删除快捷键",
        "default_menu": "默认菜单",
        "new_menu": "新建菜单",
        "new_menu_name": "菜单 {number}",
        "menu_item_count": "{count} 项",
        "rename_menu": "重命名菜单",
        "delete_menu": "删除菜单",
        "menu_name_prompt": "菜单名称",
        "delete_menu_confirm": "确定删除“{name}”吗？",
        "appearance": "外观",
        "wheel_properties": "轮盘属性",
        "open_properties": "打开轮盘属性",
        "back_to_commands": "返回命令列表",
        "shortcut": "快捷键",
        "press_key": "按一个按键",
        "reset_shortcut": "恢复默认快捷键",
        "reset_wheel_radius": "恢复默认轮盘半径",
        "reset_inner_radius": "恢复默认中心半径",
        "wheel_radius": "轮盘半径",
        "inner_radius": "中心半径",
        "language": "语言",
        "language_auto": "自动",
        "language_painter": "跟随 Painter",
        "language_zh": "中文",
        "language_en": "英文",
        "move_up": "上移",
        "move_down": "下移",
        "apply": "应用",
        "applied": "已应用",
        "reset": "恢复默认",
        "reset_confirm_title": "确认恢复默认设置",
        "reset_confirm_message": (
            "确定将全部轮盘设置恢复为默认值吗？\n\n"
            "这会替换编辑器中当前的快捷键、外观、菜单和命令布局。"
            "在点击“应用”之前，这些更改不会写入配置。"
        ),
        "close": "取消",
        "highlight_color": "高亮颜色",
        "selected_status": "当前选中：{name}",
        "editor_hint": "拖动轮盘扇区可调整位置，拖出轮盘可移除命令。",
        "move_to_position": "移动到位置 {position}",
        "release_to_remove": "松开移除",
        "restore_command": "双击恢复到轮盘",
        "category_layers": "图层",
        "category_masks": "遮罩",
        "category_effects": "效果",
        "category_adjustments": "调整",
        "category_filters": "滤镜",
        "category_other": "其他",
        "save_failed": "无法保存轮盘配置。",
        "node_fill": "轮盘填充图层",
        "node_blur": "轮盘模糊",
        "node_levels": "轮盘色阶",
        "black_mask_exists": "当前图层已经有遮罩，没有覆盖已有遮罩。",
        "anchor_point_name": "锚定点",
        "no_active_stack": "没有活动的纹理集。请先打开 Painter 项目。",
        "blur_missing": "没有在 Painter 资源库中找到 Blur/模糊滤镜。",
        "updates": "更新",
        "open_repository": "打开 GitHub 仓库",
        "auto_check_updates": "自动检查更新",
        "check_updates": "检查更新",
        "checking_updates": "正在检查...",
        "up_to_date": "V{version} 已是最新版本",
        "no_releases": "目前还没有公开发布版本",
        "update_available": "发现新版本 V{version}",
        "download_update": "下载并安装",
        "downloading_update": "正在下载... {progress}%",
        "installing_update": "正在安装...",
        "restart_to_finish": "安装完成，请重启 Painter。",
        "restart_painter": "重启 Painter",
        "restart_prompt_title": "重启 Painter",
        "restart_prompt_message": (
            "V{version} 已安装完成。是否立即重启 Painter 以载入更新？\n\n"
            "如果当前项目有未保存的修改，Painter 会询问是否保存。"
        ),
        "restart_later": "稍后重启",
        "restart_now": "立即重启",
        "update_failed": "更新失败：{detail}",
        "update_confirm_title": "安装更新",
        "update_confirm_message": (
            "确定下载并安装 V{version} 吗？\n\n"
            "你的轮盘配置会被保留。安装完成后需要重启 Painter 才会载入新版本。"
        ),
        "confirm_install": "安装",
        "invalid_version": "发布版本号无效",
        "release_missing_zip": "发布版本中没有插件 ZIP 安装包",
        "checksum_missing": "发布包没有 SHA-256 校验值",
        "checksum_mismatch": "下载包校验失败",
        "unsafe_update_redirect": "更新下载跳转到了不受信任的地址",
        "too_many_update_redirects": "更新下载重定向次数过多",
        "timeout": "连接 GitHub 超时",
        "invalid_update": "更新包无效",
        "version_mismatch": "安装包版本与发布版本不一致",
        "update_too_large": "更新包体积异常",
        "unsafe_update_path": "更新包包含不安全的文件路径",
        "unsafe_update_link": "更新包包含不安全的链接",
        "network_error": "无法连接 GitHub"
    }
}


_KEY_FILTER = None
_POPUP = None
_SETTINGS_ACTION = None
_TOOLBAR_BUTTON = None
_SETTINGS_DIALOG = None
_RUNTIME_CONFIG = None
_UPDATE_MANAGER = None
_PAINTER_LANGUAGE_CACHE = None
_ICON_PIXMAP_CACHE = {}
_RESOURCE_PIXMAP_CACHE = {}
_RESOURCE_IMAGE_PROVIDER = None
_RESOURCE_PROVIDER_RETRY_AFTER = 0.0
_RESOURCE_PRELOAD_RETRIES = 0
_RESOURCE_METADATA_INDEX = None
_RESOURCE_SIDECAR_INDEX = None
_FILTER_COMMAND_CACHE = None
_FILTER_SCAN_COMPLETE = False
_LAST_SLOT_INDEX = None
_LAST_LAYER_SLOT_INDEX = None
_LAST_WHEEL_TIME = 0.0
_LAST_WHEEL_SIGN = 0
_RESTART_LAUNCH_CALLBACK = None

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SPACE = 0x20
VK_OEM_3 = 0xC0
WM_KEYUP = 0x0101
WM_SYSKEYUP = 0x0105
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WHEEL_DELTA = 120
_NATIVE_WHEEL_FILTER = None


def _log(message):
    substance_painter.logging.log(
        substance_painter.logging.DBG_INFO,
        substance_painter.logging.PYTHON_CHANNEL,
        "[Radial Layer Tools] " + message)


def _error(message):
    substance_painter.logging.error("[Radial Layer Tools] " + message)


def _current_application_executable():
    if os.name == "nt":
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = ctypes.windll.kernel32.GetModuleFileNameW(
                None, buffer, len(buffer))
            if length:
                return os.path.realpath(buffer.value)
        except Exception:
            _error("Could not resolve Painter executable:\n" +
                   traceback.format_exc())
    return os.path.realpath(sys.executable)


def _launch_painter_detached():
    executable = _current_application_executable()
    if not executable or not os.path.isfile(executable):
        _error("Could not restart Painter: executable was not found.")
        return False
    working_directory = os.path.dirname(executable)
    helper_path = os.path.join(_plugin_root(), "restart_helper.py")
    interpreter_names = (
        ("pythonw.exe", "python.exe")
        if os.name == "nt"
        else ("python3", "python"))
    interpreter_roots = [
        str(getattr(sys, "prefix", "") or ""),
        str(getattr(sys, "base_prefix", "") or ""),
        os.path.join(working_directory, "resources", "pythonsdk")
    ]
    interpreter = ""
    for root in interpreter_roots:
        for name in interpreter_names:
            candidate = os.path.realpath(os.path.join(root, name))
            if os.path.isfile(candidate):
                interpreter = candidate
                break
        if interpreter:
            break
    if interpreter and os.path.isfile(helper_path):
        log_path = os.path.join(
            _update_cache_directory(), "restart-helper.log")
        result = QtCore.QProcess.startDetached(
            interpreter,
            [
                helper_path,
                str(os.getpid()),
                executable,
                working_directory,
                log_path
            ],
            _plugin_root())
    else:
        _error(
            "Painter restart helper was not found; starting Painter directly.")
        result = QtCore.QProcess.startDetached(
            executable, [], working_directory)
    success = bool(result[0]) if isinstance(result, tuple) else bool(result)
    if not success:
        _error("Could not start Painter after shutdown.")
    return success


def _clear_restart_launch_callback(application=None):
    global _RESTART_LAUNCH_CALLBACK
    callback = _RESTART_LAUNCH_CALLBACK
    _RESTART_LAUNCH_CALLBACK = None
    application = application or QtWidgets.QApplication.instance()
    if application is not None and callback is not None:
        try:
            application.aboutToQuit.disconnect(callback)
        except (RuntimeError, TypeError):
            pass


def _request_painter_restart():
    global _RESTART_LAUNCH_CALLBACK
    application = QtWidgets.QApplication.instance()
    main_window = substance_painter.ui.get_main_window()
    if application is None or main_window is None:
        _error("Could not restart Painter: main window was not found.")
        return False

    _clear_restart_launch_callback(application)

    def launch_after_shutdown():
        _clear_restart_launch_callback(application)
        _launch_painter_detached()

    _RESTART_LAUNCH_CALLBACK = launch_after_shutdown
    application.aboutToQuit.connect(launch_after_shutdown)
    try:
        close_accepted = bool(main_window.close())
    except Exception:
        _clear_restart_launch_callback(application)
        _error("Could not request Painter restart:\n" + traceback.format_exc())
        return False
    if not close_accepted:
        _clear_restart_launch_callback(application)
    return close_accepted


def _plugin_root():
    return os.path.dirname(os.path.abspath(__file__))


def _config_path():
    return os.path.join(_plugin_root(), CONFIG_FILE)


def _icon_path(item_id):
    filename = ICON_FILES.get(str(item_id or ""), "")
    return os.path.join(_plugin_root(), "icons", filename) if filename else ""


def _icon_pixmap(item_id, source=""):
    item_id = str(item_id or "")
    source = str(source or OFFICIAL_ICONS.get(item_id, "") or "")
    cache_key = (item_id, source)
    if cache_key not in _ICON_PIXMAP_CACHE:
        path = _icon_path(item_id)
        pixmap = QtGui.QPixmap(path) if path else QtGui.QPixmap()
        if pixmap.isNull() and source:
            icon = QtGui.QIcon(source)
            if not icon.isNull():
                pixmap = icon.pixmap(64, 64)
            if pixmap.isNull():
                pixmap = QtGui.QPixmap(source)
        _ICON_PIXMAP_CACHE[cache_key] = pixmap
    return _ICON_PIXMAP_CACHE[cache_key]


def _ensure_packaged_builtin_icons():
    icons_dir = os.path.join(_plugin_root(), "icons")
    try:
        os.makedirs(icons_dir, exist_ok=True)
    except OSError:
        return
    exported_paths = set()
    for item_id, source in OFFICIAL_ICONS.items():
        path = _icon_path(item_id)
        if not path or path in exported_paths or os.path.exists(path):
            continue
        exported_paths.add(path)
        icon = QtGui.QIcon(source)
        if icon.isNull():
            continue
        pixmap = icon.pixmap(64, 64)
        if pixmap.isNull():
            continue
        try:
            if pixmap.save(path, "PNG"):
                _log("Cached Painter icon: " + path)
        except Exception:
            _error("Failed to cache Painter icon:\n" + traceback.format_exc())


def _radial_toolbar_icon():
    size = 64
    image = QtGui.QImage(
        size, size, QtGui.QImage.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.transparent)

    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    center = QtCore.QPointF(size * 0.5, size * 0.5)
    outer_radius = 25.0
    inner_radius = 11.0
    outer_rect = QtCore.QRectF(
        center.x() - outer_radius,
        center.y() - outer_radius,
        outer_radius * 2.0,
        outer_radius * 2.0)
    inner_rect = QtCore.QRectF(
        center.x() - inner_radius,
        center.y() - inner_radius,
        inner_radius * 2.0,
        inner_radius * 2.0)

    highlight = QtGui.QPainterPath()
    highlight.arcMoveTo(outer_rect, 54.0)
    highlight.arcTo(outer_rect, 54.0, 72.0)
    highlight.arcTo(inner_rect, 126.0, -72.0)
    highlight.closeSubpath()
    painter.fillPath(highlight, QtGui.QColor("#2d8fe8"))

    line_pen = QtGui.QPen(
        QtGui.QColor("#c7c9cb"), 3.0,
        QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
    painter.setPen(line_pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawEllipse(outer_rect)
    painter.drawEllipse(inner_rect)

    for angle in (54.0, 126.0, 198.0, 270.0, 342.0):
        radians = math.radians(angle)
        start = QtCore.QPointF(
            center.x() + math.cos(radians) * inner_radius,
            center.y() - math.sin(radians) * inner_radius)
        end = QtCore.QPointF(
            center.x() + math.cos(radians) * outer_radius,
            center.y() - math.sin(radians) * outer_radius)
        painter.drawLine(start, end)

    painter.end()
    return QtGui.QIcon(QtGui.QPixmap.fromImage(image))


def _command_category(item):
    category = str(item.get("category", "") or "")
    if category in COMMAND_CATEGORY_ORDER:
        return category
    item_id = str(item.get("id", "") or "")
    if item_id in COMMAND_CATEGORY_BY_ID:
        return COMMAND_CATEGORY_BY_ID[item_id]
    action = str(item.get("action", "") or "")
    if action.startswith(FILTER_ACTION_PREFIX) or action == "add_blur_filter":
        return "filters"
    return "other"


def _painter_resource_image_provider():
    global _RESOURCE_IMAGE_PROVIDER, _RESOURCE_PROVIDER_RETRY_AFTER
    if _RESOURCE_IMAGE_PROVIDER is not None:
        try:
            _RESOURCE_IMAGE_PROVIDER.imageType()
            return _RESOURCE_IMAGE_PROVIDER
        except RuntimeError:
            _RESOURCE_IMAGE_PROVIDER = None
    now = time.monotonic()
    if now < _RESOURCE_PROVIDER_RETRY_AFTER:
        return None

    try:
        from PySide6 import QtQml, QtQuick, QtQuickWidgets
        app = QtWidgets.QApplication.instance()
        engines = list(app.findChildren(QtQml.QQmlEngine)) if app is not None else []
        if app is not None:
            for widget in app.allWidgets():
                if isinstance(widget, QtQuickWidgets.QQuickWidget):
                    engines.append(widget.engine())
        for window in QtGui.QGuiApplication.allWindows():
            if isinstance(window, QtQuick.QQuickWindow):
                engine = QtQml.qmlEngine(window.contentItem())
                if engine is not None:
                    engines.append(engine)
        seen = set()
        for engine in engines:
            if engine is None or id(engine) in seen:
                continue
            seen.add(id(engine))
            provider = engine.imageProvider("resources")
            if provider is not None:
                _RESOURCE_IMAGE_PROVIDER = provider
                return provider
    except Exception:
        pass
    _RESOURCE_PROVIDER_RETRY_AFTER = now + 2.0
    return None


def _resource_metadata_directory():
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return ""
    return os.path.join(
        local_app_data, "Adobe", "Adobe Substance 3D Painter", "cache", "metadata")


def _resource_metadata_files():
    global _RESOURCE_METADATA_INDEX
    if _RESOURCE_METADATA_INDEX is not None:
        return _RESOURCE_METADATA_INDEX

    index = {}
    directory = _resource_metadata_directory()
    try:
        filenames = os.listdir(directory)
    except OSError:
        _RESOURCE_METADATA_INDEX = index
        return index

    version_pattern = re.compile(
        rb"(?<![0-9a-f])[0-9a-f]{40}(?:\.[a-z0-9_+-]+)?",
        re.IGNORECASE)
    for filename in filenames:
        path = os.path.join(directory, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            continue
        for match in version_pattern.finditer(data):
            version = match.group(0).decode("ascii").lower()
            index.setdefault(version, path)
            index.setdefault(version.split(".", 1)[0], path)
    _RESOURCE_METADATA_INDEX = index
    return index


def _resource_asset_directories():
    candidates = []
    executable = str(getattr(sys, "executable", "") or "")
    if executable:
        candidates.append(os.path.join(
            os.path.dirname(os.path.abspath(executable)),
            "resources", "starter_assets"))
    module_path = str(getattr(sp, "__file__", "") or "")
    if module_path:
        install_root = os.path.abspath(os.path.join(
            os.path.dirname(module_path), "..", "..", "..", ".."))
        candidates.append(os.path.join(install_root, "resources", "starter_assets"))
    candidates.append(os.path.abspath(os.path.join(
        _plugin_root(), "..", "..", "..", "assets")))
    directories = []
    seen = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen or not os.path.isdir(candidate):
            continue
        seen.add(normalized)
        directories.append(candidate)
    return directories


def _resource_sidecar_files():
    global _RESOURCE_SIDECAR_INDEX
    if _RESOURCE_SIDECAR_INDEX is not None:
        return _RESOURCE_SIDECAR_INDEX

    index = {}
    for root in _resource_asset_directories():
        for directory, _subdirectories, filenames in os.walk(root):
            if os.path.basename(directory).lower() != ".alg_meta":
                continue
            for filename in filenames:
                index.setdefault(filename.casefold(), []).append(
                    os.path.join(directory, filename))
    _RESOURCE_SIDECAR_INDEX = index
    return index


def _resource_metadata_path(item):
    version = str(item.get("resource_version", "") or "").lower()
    if not version:
        return ""
    metadata_files = _resource_metadata_files()
    path = metadata_files.get(version)
    if path is None:
        path = metadata_files.get(version.split(".", 1)[0])
    if path:
        return path

    resource_name = str(item.get("resource_name", "") or "")
    package_name = resource_name.split("/", 1)[0]
    extension = os.path.splitext(version)[1] or ".sbsar"
    filename = (package_name + extension).casefold()
    candidates = _resource_sidecar_files().get(filename, [])
    version_bytes = version.encode("ascii", errors="ignore")
    for candidate in candidates:
        try:
            with open(candidate, "rb") as handle:
                data = handle.read()
        except OSError:
            continue
        if not version_bytes or version_bytes in data.lower():
            return candidate
    return candidates[0] if len(candidates) == 1 else ""


def _embedded_image_data(data, start=0):
    signatures = (
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"RIFF", "webp"),
        (b"\xff\xd8\xff", "jpeg"))
    positions = []
    for signature, image_type in signatures:
        position = data.find(signature, max(0, int(start)))
        if position >= 0:
            positions.append((position, image_type))
    if not positions:
        return b""

    position, image_type = min(positions, key=lambda entry: entry[0])
    if image_type == "webp":
        if position + 12 > len(data) or data[position + 8:position + 12] != b"WEBP":
            return b""
        end = position + 8 + int.from_bytes(
            data[position + 4:position + 8], "little")
        return data[position:end] if end <= len(data) else b""
    if image_type == "jpeg":
        end = data.find(b"\xff\xd9", position + 3)
        return data[position:end + 2] if end >= 0 else b""

    cursor = position + 8
    while cursor + 12 <= len(data):
        chunk_length = int.from_bytes(data[cursor:cursor + 4], "big")
        chunk_type = data[cursor + 4:cursor + 8]
        cursor += 12 + chunk_length
        if cursor > len(data):
            return b""
        if chunk_type == b"IEND":
            return data[position:cursor]
    return b""


def _metadata_preview_pixmap(item):
    version = str(item.get("resource_version", "") or "").lower()
    resource_name = str(item.get("resource_name", "") or "")
    if not version or not resource_name:
        return QtGui.QPixmap()

    metadata_path = _resource_metadata_path(item)
    if not metadata_path:
        return QtGui.QPixmap()
    try:
        with open(metadata_path, "rb") as handle:
            data = handle.read()
    except OSError:
        return QtGui.QPixmap()

    starts = []
    for suffix in ("custom-preview", "previews"):
        marker = ("graph-id-%s-%s" % (resource_name, suffix)).encode("utf-16-be")
        position = data.find(marker)
        if position >= 0:
            starts.append(position + len(marker))
    if not starts:
        return QtGui.QPixmap()

    image_data = _embedded_image_data(data, min(starts))
    if not image_data:
        return QtGui.QPixmap()
    pixmap = QtGui.QPixmap()
    if pixmap.loadFromData(image_data):
        return pixmap
    return QtGui.QPixmap()


def _resource_preview_pixmap(item, size=96):
    resource_url = str(item.get("resource_url", "") or "")
    if not resource_url:
        return QtGui.QPixmap()
    if resource_url in _RESOURCE_PIXMAP_CACHE:
        return _RESOURCE_PIXMAP_CACHE[resource_url]

    pixmap = _metadata_preview_pixmap(item)
    if not pixmap.isNull():
        _RESOURCE_PIXMAP_CACHE[resource_url] = pixmap
        return pixmap

    provider = _painter_resource_image_provider()
    if provider is None:
        return QtGui.QPixmap()
    try:
        from PySide6 import QtQuick
        actual_size = QtCore.QSize()
        requested_size = QtCore.QSize(int(size), int(size))
        if provider.imageType() == QtQuick.QQuickImageProvider.Pixmap:
            pixmap = provider.requestPixmap(
                resource_url, actual_size, requested_size)
        else:
            image = provider.requestImage(
                resource_url, actual_size, requested_size)
            pixmap = QtGui.QPixmap.fromImage(image)
        if pixmap is not None and not pixmap.isNull():
            _RESOURCE_PIXMAP_CACHE[resource_url] = pixmap
            return pixmap
    except Exception:
        pass
    return QtGui.QPixmap()


def _item_icon_pixmap(item, load_resource=False):
    resource_url = str(item.get("resource_url", "") or "")
    if resource_url:
        pixmap = _RESOURCE_PIXMAP_CACHE.get(resource_url, QtGui.QPixmap())
        if pixmap.isNull() and load_resource:
            pixmap = _resource_preview_pixmap(item)
        if not pixmap.isNull():
            return pixmap
        if item.get("resource_version") and not load_resource:
            return QtGui.QPixmap()
    pixmap = _icon_pixmap(item.get("id", ""), item.get("icon", ""))
    if pixmap.isNull() and _command_category(item) == "filters":
        pixmap = _icon_pixmap("blur")
    return pixmap


def _stable_resource_url(resource_id):
    stable_id = sp.resource.ResourceID(
        context=str(resource_id.context),
        name=str(resource_id.name))
    return stable_id.url()


def _filter_command_id(resource_url):
    digest = hashlib.sha1(resource_url.encode("utf-8")).hexdigest()[:12]
    return "filter_" + digest


def _filter_labels(name):
    english = str(name or "").strip()
    chinese = FILTER_ZH_LABELS.get(english.casefold(), english)
    return {"en": english, "zh_CN": chinese}


def _discover_filter_commands(force=False):
    global _FILTER_COMMAND_CACHE, _FILTER_SCAN_COMPLETE
    if _FILTER_COMMAND_CACHE is not None and not force:
        return copy.deepcopy(_FILTER_COMMAND_CACHE)

    commands = []
    seen_urls = set()
    scan_complete = True
    try:
        resources = sp.resource.search("u:filter")
    except Exception:
        _FILTER_SCAN_COMPLETE = False
        _error("Failed to scan Painter filter resources:\n" + traceback.format_exc())
        return []

    for resource in resources:
        try:
            resource_id = resource.identifier()
            resource_url = _stable_resource_url(resource_id)
            if resource_url in seen_urls:
                continue
            seen_urls.add(resource_url)
            name = str(resource.gui_name() or resource_id.name).strip()
            normalized_name = name.casefold()
            resource_name = str(resource_id.name or "").strip().casefold()
            is_blur = (
                str(resource_id.context or "").casefold() == "starter_assets"
                and resource_name in ("blur", "blur/blur")
                and normalized_name in ("blur", "模糊"))
            commands.append({
                "id": "blur" if is_blur else _filter_command_id(resource_url),
                "labels": _filter_labels(name),
                "descriptions": {
                    "en": "Painter filter: %s" % name,
                    "zh_CN": "Painter 滤镜：%s" % _filter_labels(name)["zh_CN"]
                },
                "action": "add_blur_filter" if is_blur else FILTER_ACTION_PREFIX + resource_url,
                "category": "filters",
                "icon": "style:views/icons/effects_substance.svg",
                "resource_url": resource_url,
                "resource_name": str(resource_id.name or ""),
                "resource_version": str(resource_id.version or ""),
                "enabled": True if is_blur else False
            })
        except Exception:
            scan_complete = False
            _error("Skipped an unreadable Painter filter resource:\n" + traceback.format_exc())

    commands.sort(key=lambda item: str(item["labels"]["en"]).casefold())
    _FILTER_COMMAND_CACHE = copy.deepcopy(commands)
    _FILTER_SCAN_COMPLETE = scan_complete
    return commands


def _merge_filter_catalog(config, force=False):
    items = list(config.get("items", []))
    commands = _discover_filter_commands(force=force)
    discovered_actions = {
        str(command.get("action", "")) for command in commands}
    removed_ids = []
    if _FILTER_SCAN_COMPLETE:
        kept_items = []
        for item in items:
            action = str(item.get("action", ""))
            if (
                    action.startswith(FILTER_ACTION_PREFIX)
                    and action not in discovered_actions):
                item_id = str(item.get("id", ""))
                if item_id:
                    removed_ids.append(item_id)
                resource_url = str(item.get("resource_url", ""))
                if resource_url:
                    _RESOURCE_PIXMAP_CACHE.pop(resource_url, None)
                continue
            kept_items.append(item)
        items = kept_items
    by_action = {
        str(item.get("action", "")): item
        for item in items
        if item.get("action")
    }
    for item in items:
        item["category"] = _command_category(item)
    for command in commands:
        action = command["action"]
        if action in by_action:
            existing = by_action[action]
            existing["category"] = "filters"
            existing["labels"] = copy.deepcopy(command["labels"])
            existing["descriptions"] = copy.deepcopy(command["descriptions"])
            for key in ("resource_url", "resource_name", "resource_version"):
                if command.get(key):
                    existing[key] = command[key]
            continue
        items.append(command)
        by_action[action] = command
    config["items"] = items
    return removed_ids


def _merge_builtin_commands(config):
    items = list(config.get("items", []))
    known_ids = {
        str(item.get("id", "")) for item in items if item.get("id")}
    known_actions = {
        str(item.get("action", "")) for item in items if item.get("action")}
    for command in DEFAULT_CONFIG["items"]:
        command_id = str(command.get("id", ""))
        action = str(command.get("action", ""))
        if command_id in known_ids or action in known_actions:
            continue
        items.append(copy.deepcopy(command))
        known_ids.add(command_id)
        known_actions.add(action)
    config["items"] = items
    return config


def _save_config(config):
    path = _config_path()
    temporary_path = path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, path)


def _load_config():
    path = _config_path()
    if not os.path.exists(path):
        _save_config(DEFAULT_CONFIG)
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except Exception:
        _error("Failed to read config, using defaults:\n" + traceback.format_exc())
        return copy.deepcopy(DEFAULT_CONFIG)

    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    if "shortcut" in config:
        merged["shortcut"] = dict(DEFAULT_CONFIG["shortcut"])
        merged["shortcut"].update(config["shortcut"])
    if "items" not in config:
        merged["items"] = list(DEFAULT_CONFIG["items"])
    merged = _merge_builtin_commands(merged)
    _normalize_command_shortcuts(merged)
    return merged


def _normalize_command_shortcuts(config):
    known_ids = {
        str(item.get("id", ""))
        for item in config.get("items", [])
        if item.get("id")
    }
    normalized = []
    seen_ids = set()
    entries = config.get("command_shortcuts", [])
    if not isinstance(entries, list):
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        command_id = str(entry.get("command_id", "") or "")
        if not command_id or command_id not in known_ids or command_id in seen_ids:
            continue
        shortcut = entry.get("shortcut", {})
        if not isinstance(shortcut, dict):
            shortcut = {}
        modifiers = []
        for modifier in shortcut.get("modifiers", []):
            name = str(modifier or "").strip().lower()
            canonical = {
                "control": "Ctrl", "ctrl": "Ctrl",
                "alt": "Alt", "option": "Alt",
                "shift": "Shift"
            }.get(name)
            if canonical and canonical not in modifiers:
                modifiers.append(canonical)
        normalized.append({
            "command_id": command_id,
            "shortcut": {
                "key": str(shortcut.get("key", "") or ""),
                "modifiers": modifiers
            }
        })
        seen_ids.add(command_id)
    config["command_shortcuts"] = normalized
    return config


def _normalize_menu_presets(config):
    config.pop("center_label", None)
    items = list(config.get("items", []))
    known_ids = {
        str(item.get("id", "")) for item in items if item.get("id")}
    menus = config.get("menus")
    if not isinstance(menus, list) or not menus:
        active_ids = [
            str(item.get("id", ""))
            for item in items
            if item.get("id") and item.get("enabled", True)]
        menus = [{
            "id": "default",
            "names": {"en": "Default Menu", "zh_CN": "默认菜单"},
            "item_ids": active_ids
        }]

    normalized = []
    seen_ids = set()
    for index, menu in enumerate(menus):
        if not isinstance(menu, dict):
            continue
        menu_id = str(menu.get("id", "") or "").strip()
        if not menu_id or menu_id in seen_ids:
            menu_id = "menu_%d" % (index + 1)
        seen_ids.add(menu_id)
        item_ids = []
        for item_id in menu.get("item_ids", []):
            item_id = str(item_id or "")
            if item_id in known_ids and item_id not in item_ids:
                item_ids.append(item_id)
        normalized_menu = copy.deepcopy(menu)
        normalized_menu["id"] = menu_id
        normalized_menu["item_ids"] = item_ids
        normalized_menu.pop("center_label", None)
        normalized.append(normalized_menu)

    if not normalized:
        config.pop("menus", None)
        return _normalize_menu_presets(config)
    config["menus"] = normalized
    active_id = str(config.get("active_menu_id", "") or "")
    if active_id not in {menu["id"] for menu in normalized}:
        active_id = normalized[0]["id"]
    config["active_menu_id"] = active_id
    return config


def _menu_preset_name(menu, config):
    custom_name = str(menu.get("name", "") or "").strip()
    if custom_name:
        return custom_name
    names = menu.get("names", {})
    if isinstance(names, dict):
        language = _language(config)
        return str(
            names.get(language) or names.get("en")
            or next(iter(names.values()), _tr("default_menu", config)))
    return _tr("default_menu", config)


def _active_menu_name(config):
    active_id = str(config.get("active_menu_id", "") or "")
    for menu in config.get("menus", []):
        if str(menu.get("id", "")) == active_id:
            return _menu_preset_name(menu, config)
    return _tr("default_menu", config)


def _update_cache_directory():
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    base = local_app_data or os.path.expanduser("~")
    return os.path.join(base, "RadialLayerTools", "updates")


def _update_state_path():
    return os.path.join(_update_cache_directory(), "state.json")


def _load_update_state():
    try:
        with open(_update_state_path(), "r", encoding="utf-8") as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        _error("Failed to read update state:\n" + traceback.format_exc())
        return {}


def _save_update_state(state):
    directory = _update_cache_directory()
    os.makedirs(directory, exist_ok=True)
    path = _update_state_path()
    temporary_path = path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_path, path)


def _version_tuple(value):
    match = re.fullmatch(
        r"\s*[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?\s*",
        str(value or ""))
    if match is None:
        return ()
    return tuple(int(part or 0) for part in match.groups())


def _network_request_attribute(name):
    value = getattr(QtNetwork.QNetworkRequest, name, None)
    if value is not None:
        return value
    enum_type = getattr(QtNetwork.QNetworkRequest, "Attribute", None)
    return getattr(enum_type, name, None) if enum_type is not None else None


def _network_redirect_policy(name):
    value = getattr(QtNetwork.QNetworkRequest, name, None)
    if value is not None:
        return value
    enum_type = getattr(QtNetwork.QNetworkRequest, "RedirectPolicy", None)
    return getattr(enum_type, name, None) if enum_type is not None else None


def _network_redirect_target(reply):
    attribute = _network_request_attribute("RedirectionTargetAttribute")
    try:
        target = reply.attribute(attribute) if attribute is not None else None
        target_url = QtCore.QUrl(target) if target is not None else QtCore.QUrl()
        if not target_url.isValid() or target_url.isEmpty():
            location = bytes(reply.rawHeader(b"Location")).decode(
                "utf-8", errors="replace").strip()
            target_url = QtCore.QUrl(location)
        if not target_url.isValid() or target_url.isEmpty():
            return QtCore.QUrl()
        return reply.url().resolved(target_url)
    except Exception:
        return QtCore.QUrl()


def _is_trusted_update_url(url):
    candidate = url if isinstance(url, QtCore.QUrl) else QtCore.QUrl(str(url))
    if not candidate.isValid() or candidate.scheme().casefold() != "https":
        return False
    host = candidate.host().casefold()
    return (
        host in {"github.com", "api.github.com"}
        or host.endswith(".githubusercontent.com")
    )


def _network_no_error():
    value = getattr(QtNetwork.QNetworkReply, "NoError", None)
    if value is not None:
        return value
    enum_type = getattr(QtNetwork.QNetworkReply, "NetworkError", None)
    return getattr(enum_type, "NoError", 0) if enum_type is not None else 0


def _safe_extract_update(archive_path, destination):
    destination_root = os.path.realpath(destination)
    with zipfile.ZipFile(archive_path, "r") as archive:
        members = archive.infolist()
        if len(members) > 500:
            raise ValueError("update_too_large")
        total_size = sum(max(0, int(member.file_size)) for member in members)
        if total_size > 100 * 1024 * 1024:
            raise ValueError("update_too_large")
        for member in members:
            name = str(member.filename or "").replace("\\", "/")
            if not name:
                continue
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                raise ValueError("unsafe_update_path")
            target = os.path.realpath(os.path.join(destination_root, name))
            try:
                inside_destination = os.path.commonpath(
                    [destination_root, target]) == destination_root
            except ValueError:
                inside_destination = False
            if not inside_destination:
                raise ValueError("unsafe_update_path")
            file_type = (int(member.external_attr) >> 16) & 0o170000
            if file_type == 0o120000:
                raise ValueError("unsafe_update_link")
        archive.extractall(destination_root)


def _find_update_package_root(extract_directory):
    candidates = [
        os.path.join(extract_directory, "radial_layer_tools"),
        extract_directory
    ]
    for root, directories, filenames in os.walk(extract_directory):
        del directories
        if "__init__.py" in filenames:
            candidates.append(root)
    seen = set()
    for candidate in candidates:
        candidate = os.path.realpath(candidate)
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isfile(os.path.join(candidate, "__init__.py")):
            return candidate
    return ""


def _package_version(package_root):
    source_path = os.path.join(package_root, "__init__.py")
    with open(source_path, "r", encoding="utf-8") as handle:
        source = handle.read(8192)
    match = re.search(
        r"^PLUGIN_VERSION\s*=\s*['\"]([^'\"]+)['\"]",
        source, re.MULTILINE)
    return match.group(1) if match else ""


def _iter_install_files(root):
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = [
            name for name in subdirectories if name != "__pycache__"]
        for filename in filenames:
            if filename == CONFIG_FILE or filename.endswith((".pyc", ".pyo")):
                continue
            source_path = os.path.join(directory, filename)
            relative_path = os.path.relpath(source_path, root)
            yield source_path, relative_path


def _copy_file_atomically(source_path, destination_path):
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    temporary_path = destination_path + ".update_tmp"
    try:
        shutil.copy2(source_path, temporary_path)
        os.replace(temporary_path, destination_path)
    finally:
        if os.path.isfile(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass


def _backup_current_plugin(destination):
    for source_path, relative_path in _iter_install_files(_plugin_root()):
        target_path = os.path.join(destination, relative_path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(source_path, target_path)


def _restore_plugin_backup(backup_directory):
    if not backup_directory or not os.path.isdir(backup_directory):
        return
    for source_path, relative_path in _iter_install_files(backup_directory):
        _copy_file_atomically(
            source_path, os.path.join(_plugin_root(), relative_path))


class UpdateManager(QtCore.QObject):
    stateChanged = QtCore.Signal()
    progressChanged = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.detail = ""
        self.progress = -1
        self.latest_version = ""
        self.release_notes = ""
        self._release = {}
        self._reply = None
        self._purpose = ""
        self._redirect_count = 0
        self._timed_out = False
        self._shutting_down = False
        self._restart_prompt_open = False
        self._network = QtNetwork.QNetworkAccessManager(self)
        self._timeout_timer = QtCore.QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._timeout_active_request)
        self._automatic_timer = QtCore.QTimer(self)
        self._automatic_timer.setSingleShot(True)
        self._automatic_timer.timeout.connect(self.check_automatic)
        self._cached_state = _load_update_state()
        self._apply_cached_state()

    def schedule_automatic_check(self, delay_ms=2500):
        if self._shutting_down or self._automatic_timer.isActive():
            return
        self._automatic_timer.start(max(0, int(delay_ms)))

    def _set_state(self, state, detail="", progress=None):
        self.state = str(state)
        self.detail = str(detail or "")
        if progress is not None:
            self.progress = int(progress)
            self.progressChanged.emit(self.progress)
        self.stateChanged.emit()

    def _apply_cached_state(self):
        installed_version = str(
            self._cached_state.get("installed_version", "") or "")
        pending_restart = bool(self._cached_state.get("pending_restart", False))
        if pending_restart and _version_tuple(installed_version) > _version_tuple(
                PLUGIN_VERSION):
            self.latest_version = installed_version
            self._set_state("restart")
            return
        if pending_restart and installed_version == PLUGIN_VERSION:
            self._cached_state["pending_restart"] = False
            self._cached_state["result"] = "latest"
            try:
                _save_update_state(self._cached_state)
            except Exception:
                _error("Failed to finalize update state:\n" + traceback.format_exc())

        self.latest_version = str(
            self._cached_state.get("latest_version", "") or "")
        self.release_notes = str(
            self._cached_state.get("release_notes", "") or "")
        self._release = {
            "version": self.latest_version,
            "asset_name": str(
                self._cached_state.get("asset_name", "") or ""),
            "asset_url": str(
                self._cached_state.get("asset_url", "") or ""),
            "asset_digest": str(
                self._cached_state.get("asset_digest", "") or "")
        }
        result = str(self._cached_state.get("result", "") or "")
        if result == "no_releases":
            self._set_state("no_releases")
        elif self.latest_version:
            self._apply_release_state()

    def _apply_release_state(self):
        remote = _version_tuple(self.latest_version)
        current = _version_tuple(PLUGIN_VERSION)
        if not remote:
            self._set_state("error", "invalid_version")
        elif remote > current:
            if not self._release.get("asset_url"):
                self._set_state("error", "release_missing_zip")
            elif not str(self._release.get("asset_digest", "")).startswith(
                    "sha256:"):
                self._set_state("error", "checksum_missing")
            else:
                self._set_state("available")
        else:
            self._set_state("latest")

    def check_automatic(self):
        try:
            config = _load_config()
            if not bool(config.get("auto_check_updates", True)):
                return
            self.check_for_updates(force=False)
        except Exception:
            _error("Automatic update check failed:\n" + traceback.format_exc())

    def check_for_updates(self, force=True):
        if self._reply is not None or self.state in ("downloading", "installing"):
            return
        if self.state == "restart":
            self.stateChanged.emit()
            return
        last_checked = float(self._cached_state.get("last_checked", 0.0) or 0.0)
        if (
                not force
                and last_checked > 0
                and time.time() - last_checked < UPDATE_CHECK_INTERVAL_SECONDS):
            self._apply_cached_state()
            return
        self.latest_version = ""
        self._release = {}
        self._set_state("checking", progress=-1)
        self._start_request(GITHUB_LATEST_RELEASE_API, "check")

    def download_and_install(self):
        if self.state != "available" or self._reply is not None:
            return
        url = str(self._release.get("asset_url", "") or "")
        if not url:
            self._set_state("error", "release_missing_zip")
            return
        self._set_state("downloading", progress=0)
        self._start_request(url, "download")

    def prompt_restart(self, parent=None):
        if (
                self._shutting_down
                or self.state != "restart"
                or self._restart_prompt_open):
            return
        config = _load_config()
        parent = (
            parent
            or QtWidgets.QApplication.activeWindow()
            or substance_painter.ui.get_main_window())
        dialog = QtWidgets.QMessageBox(parent)
        dialog.setWindowTitle(_tr("restart_prompt_title", config))
        dialog.setIcon(QtWidgets.QMessageBox.Information)
        dialog.setTextFormat(QtCore.Qt.PlainText)
        dialog.setText(_tr("restart_prompt_message", config).format(
            version=self.latest_version or PLUGIN_VERSION))
        later_button = dialog.addButton(
            _tr("restart_later", config),
            QtWidgets.QMessageBox.RejectRole)
        restart_button = dialog.addButton(
            _tr("restart_now", config),
            QtWidgets.QMessageBox.AcceptRole)
        dialog.setDefaultButton(later_button)
        dialog.setEscapeButton(later_button)
        self._restart_prompt_open = True
        try:
            dialog.exec()
        finally:
            self._restart_prompt_open = False
        if dialog.clickedButton() is restart_button:
            QtCore.QTimer.singleShot(0, _request_painter_restart)

    def _start_request(self, url, purpose, redirect_count=0):
        request_url = QtCore.QUrl(str(url))
        if not _is_trusted_update_url(request_url):
            self._set_state("error", "unsafe_update_redirect")
            return
        request = QtNetwork.QNetworkRequest(request_url)
        request.setRawHeader(
            b"User-Agent", ("RadialLayerTools/" + PLUGIN_VERSION).encode("ascii"))
        accept = (
            b"application/octet-stream"
            if str(purpose) == "download"
            else b"application/vnd.github+json"
        )
        request.setRawHeader(b"Accept", accept)
        request.setRawHeader(b"X-GitHub-Api-Version", b"2022-11-28")
        redirect_attribute = _network_request_attribute("RedirectPolicyAttribute")
        redirect_policy = _network_redirect_policy("NoLessSafeRedirectPolicy")
        if redirect_attribute is not None and redirect_policy is not None:
            request.setAttribute(redirect_attribute, redirect_policy)
        self._purpose = str(purpose)
        self._redirect_count = max(0, int(redirect_count))
        self._timed_out = False
        self._reply = self._network.get(request)
        self._reply.finished.connect(self._request_finished)
        if self._purpose == "download":
            self._reply.downloadProgress.connect(self._download_progress)
        self._timeout_timer.start(UPDATE_REQUEST_TIMEOUT_MS)

    def _http_status(self, reply):
        attribute = _network_request_attribute("HttpStatusCodeAttribute")
        if attribute is None:
            return 0
        try:
            value = reply.attribute(attribute)
            return int(value) if value is not None else 0
        except Exception:
            return 0

    def _request_finished(self):
        reply = self._reply
        if reply is None:
            return
        self._timeout_timer.stop()
        purpose = self._purpose
        redirect_count = self._redirect_count
        redirect_url = _network_redirect_target(reply)
        self._reply = None
        self._purpose = ""
        self._redirect_count = 0
        status = self._http_status(reply)
        data = bytes(reply.readAll())
        error = reply.error()
        error_text = reply.errorString()
        reply.deleteLater()
        if self._shutting_down:
            return
        if status in (301, 302, 303, 307, 308):
            if not redirect_url.isValid() or redirect_url.isEmpty():
                self._set_state("error", "unsafe_update_redirect")
                return
            if redirect_count >= 5:
                self._set_state("error", "too_many_update_redirects")
                return
            if not _is_trusted_update_url(redirect_url):
                self._set_state("error", "unsafe_update_redirect")
                return
            self._start_request(
                redirect_url.toString(), purpose, redirect_count + 1)
            return
        if purpose == "check" and status == 404:
            self._cache_no_releases()
            return
        if self._timed_out:
            self._set_state("error", "timeout")
            return
        if error != _network_no_error() or status >= 400:
            self._set_state("error", error_text or "network_error")
            return
        try:
            if purpose == "check":
                self._handle_release_response(data)
            elif purpose == "download":
                self._handle_download_response(data)
        except Exception:
            _error("Update request failed:\n" + traceback.format_exc())
            self._set_state("error", "invalid_update")

    def _handle_release_response(self, data):
        payload = json.loads(data.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid_release")
        version = str(payload.get("tag_name", "") or "").lstrip("vV")
        if not _version_tuple(version):
            raise ValueError("invalid_version")
        assets = payload.get("assets", [])
        assets = assets if isinstance(assets, list) else []
        zip_assets = [
            asset for asset in assets
            if isinstance(asset, dict)
            and str(asset.get("name", "")).lower().endswith(".zip")]
        preferred = [
            asset for asset in zip_assets
            if "radiallayertools" in re.sub(
                r"[^a-z0-9]", "", str(asset.get("name", "")).lower())]
        asset = (preferred or zip_assets or [{}])[0]
        self.latest_version = version
        self.release_notes = str(payload.get("body", "") or "")[:8000]
        self._release = {
            "version": version,
            "asset_name": str(asset.get("name", "") or ""),
            "asset_url": str(asset.get("browser_download_url", "") or ""),
            "asset_digest": str(asset.get("digest", "") or "").lower()
        }
        self._cached_state.update({
            "last_checked": time.time(),
            "result": "release",
            "latest_version": self.latest_version,
            "release_notes": self.release_notes,
            "asset_name": self._release["asset_name"],
            "asset_url": self._release["asset_url"],
            "asset_digest": self._release["asset_digest"]
        })
        _save_update_state(self._cached_state)
        self._apply_release_state()

    def _cache_no_releases(self):
        self.latest_version = ""
        self.release_notes = ""
        self._release = {}
        self._cached_state.update({
            "last_checked": time.time(),
            "result": "no_releases",
            "latest_version": "",
            "release_notes": "",
            "asset_name": "",
            "asset_url": "",
            "asset_digest": ""
        })
        try:
            _save_update_state(self._cached_state)
        except Exception:
            _error("Failed to cache update result:\n" + traceback.format_exc())
        self._set_state("no_releases", progress=-1)

    def _download_progress(self, received, total):
        if total <= 0:
            return
        progress = max(0, min(100, int(received * 100 / total)))
        if progress != self.progress:
            self.progress = progress
            self.progressChanged.emit(progress)

    def _handle_download_response(self, data):
        expected_digest = str(
            self._release.get("asset_digest", "") or "").lower()
        actual_digest = "sha256:" + hashlib.sha256(data).hexdigest().lower()
        if expected_digest != actual_digest:
            _error(
                "Update checksum mismatch: expected %s, received %s (%d bytes)"
                % (expected_digest, actual_digest, len(data)))
            self._set_state("error", "checksum_mismatch")
            return
        self._set_state("installing", progress=100)
        QtCore.QTimer.singleShot(
            0, lambda payload=data: self._install_download(payload))

    def _install_download(self, data):
        backup_directory = ""
        backup_ready = False
        extract_directory = ""
        installed_paths = []
        existing_paths = set()
        try:
            cache_directory = _update_cache_directory()
            downloads_directory = os.path.join(cache_directory, "downloads")
            os.makedirs(downloads_directory, exist_ok=True)
            asset_name = os.path.basename(str(
                self._release.get("asset_name", "") or "update.zip"))
            archive_path = os.path.join(downloads_directory, asset_name)
            temporary_archive = archive_path + ".tmp"
            with open(temporary_archive, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_archive, archive_path)

            extract_directory = os.path.join(
                cache_directory, "extract-%d" % int(time.time() * 1000))
            os.makedirs(extract_directory, exist_ok=False)
            _safe_extract_update(archive_path, extract_directory)
            package_root = _find_update_package_root(extract_directory)
            if not package_root:
                raise ValueError("invalid_update")
            package_version = _package_version(package_root)
            if _version_tuple(package_version) != _version_tuple(
                    self.latest_version):
                raise ValueError("version_mismatch")

            backup_directory = os.path.join(
                cache_directory,
                "backups",
                "V%s-%s" % (
                    PLUGIN_VERSION, time.strftime("%Y%m%d-%H%M%S")))
            os.makedirs(backup_directory, exist_ok=False)
            _backup_current_plugin(backup_directory)
            backup_ready = True
            existing_paths = {
                os.path.normcase(os.path.normpath(relative_path))
                for unused_source, relative_path in _iter_install_files(
                    _plugin_root())
            }
            for source_path, relative_path in _iter_install_files(package_root):
                destination_path = os.path.join(_plugin_root(), relative_path)
                _copy_file_atomically(source_path, destination_path)
                installed_paths.append((destination_path, relative_path))

            self._cached_state.update({
                "pending_restart": True,
                "installed_version": self.latest_version,
                "backup_directory": backup_directory,
                "installed_at": time.time()
            })
            _save_update_state(self._cached_state)
            self._set_state("restart", progress=100)
            _log("Installed update V%s; restart Painter to load it." %
                 self.latest_version)
            QtCore.QTimer.singleShot(0, self.prompt_restart)
        except Exception as exception:
            if backup_ready:
                try:
                    for destination_path, relative_path in installed_paths:
                        normalized = os.path.normcase(os.path.normpath(
                            relative_path))
                        if normalized not in existing_paths:
                            try:
                                os.remove(destination_path)
                            except OSError:
                                pass
                    _restore_plugin_backup(backup_directory)
                except Exception:
                    _error("Update rollback failed:\n" + traceback.format_exc())
            _error("Update installation failed:\n" + traceback.format_exc())
            detail = str(exception or "invalid_update")
            self._set_state("error", detail)
        finally:
            if extract_directory and os.path.isdir(extract_directory):
                try:
                    shutil.rmtree(extract_directory)
                except Exception:
                    _error("Failed to clean update staging directory:\n" +
                           traceback.format_exc())

    def _timeout_active_request(self):
        if self._reply is None:
            return
        self._timed_out = True
        self._reply.abort()

    def shutdown(self):
        self._shutting_down = True
        self._automatic_timer.stop()
        self._timeout_timer.stop()
        reply = self._reply
        self._reply = None
        if reply is not None:
            try:
                reply.finished.disconnect(self._request_finished)
            except (RuntimeError, TypeError):
                pass
            if self._purpose == "download":
                try:
                    reply.downloadProgress.disconnect(self._download_progress)
                except (RuntimeError, TypeError):
                    pass
            try:
                reply.abort()
                reply.deleteLater()
            except RuntimeError:
                pass
        self._purpose = ""


def _language_code(value):
    text = str(value or "").strip().lower().replace("-", "_")
    if text.startswith("zh") or "chinese" in text or "中文" in text:
        return "zh_CN"
    if text.startswith("en") or "english" in text or "英文" in text:
        return "en"
    return ""


def _painter_language():
    global _PAINTER_LANGUAGE_CACHE
    if _PAINTER_LANGUAGE_CACHE:
        return _PAINTER_LANGUAGE_CACHE

    def cache(value):
        global _PAINTER_LANGUAGE_CACHE
        _PAINTER_LANGUAGE_CACHE = value
        return value

    app = QtWidgets.QApplication.instance()
    objects = [app]
    try:
        main_window = substance_painter.ui.get_main_window()
    except Exception:
        main_window = None
    if main_window is not None:
        objects.append(main_window)

    property_names = (
        "language", "uiLanguage", "applicationLanguage", "currentLanguage")
    for obj in objects:
        if obj is None:
            continue
        names = list(property_names)
        try:
            names.extend(bytes(name).decode("utf-8") for name in obj.dynamicPropertyNames())
        except Exception:
            pass
        for name in names:
            try:
                detected = _language_code(obj.property(name))
            except Exception:
                detected = ""
            if detected:
                return cache(detected)

    if main_window is not None:
        try:
            action_texts = [
                str(action.text()).replace("&", "")
                for action in main_window.findChildren(QtGui.QAction)
                if action is not _SETTINGS_ACTION
            ]
            painter_menu_markers = ("文件", "编辑", "窗口", "帮助")
            marker_count = sum(
                1 for marker in painter_menu_markers
                if any(marker in text for text in action_texts))
            if marker_count >= 2:
                return cache("zh_CN")
        except Exception:
            pass

        try:
            detected = _language_code(main_window.locale().name())
            if detected:
                return cache(detected)
        except Exception:
            pass

    for locale in (QtCore.QLocale(), QtCore.QLocale.system()):
        detected = _language_code(locale.name())
        if detected:
            return cache(detected)
    return cache("en")


def _language(config=None):
    config = config or _load_config()
    value = str(config.get("language", "auto")).strip().lower()
    if value not in ("", "auto", "painter", "software"):
        return "zh_CN" if value.startswith("zh") else "en"
    return _painter_language()


def _tr(key, config=None):
    lang = _language(config)
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, key)


def _localized_value(item, field, config):
    value = item.get(field)
    if isinstance(value, dict):
        lang = _language(config)
        return value.get(lang) or value.get("en") or next(iter(value.values()), "")
    if value:
        return str(value)
    legacy = "label" if field == "labels" else "description"
    return str(item.get(legacy, ""))


def _shortcut_sequence_from_value(shortcut):
    shortcut = shortcut if isinstance(shortcut, dict) else {}
    key = str(shortcut.get("key", "") or "")
    if not key:
        return QtGui.QKeySequence()
    parts = [str(item) for item in shortcut.get("modifiers", [])]
    parts.append(key)
    return QtGui.QKeySequence("+".join(parts))


def _shortcut_sequence(config):
    return _shortcut_sequence_from_value(config.get("shortcut", {}))


def _shortcut_value_from_sequence(sequence, fallback=None):
    sequence = QtGui.QKeySequence(sequence)
    if sequence.isEmpty():
        return copy.deepcopy(fallback or {"key": "", "modifiers": []})
    try:
        combination = sequence[0]
        key_sequence = QtGui.QKeySequence(combination.key())
        key = key_sequence.toString(QtGui.QKeySequence.PortableText)
        modifiers_value = combination.keyboardModifiers()
        modifiers = []
        if modifiers_value & QtCore.Qt.ControlModifier:
            modifiers.append("Ctrl")
        if modifiers_value & QtCore.Qt.AltModifier:
            modifiers.append("Alt")
        if modifiers_value & QtCore.Qt.ShiftModifier:
            modifiers.append("Shift")
        if key:
            return {"key": key, "modifiers": modifiers}
    except Exception:
        pass
    text = sequence.toString(QtGui.QKeySequence.PortableText)
    if not text:
        return copy.deepcopy(fallback or {"key": "", "modifiers": []})
    parts = [part.strip() for part in text.split("+") if part.strip()]
    key = parts[-1] if parts else ""
    aliases = {
        "Control": "Ctrl", "Ctrl": "Ctrl", "Alt": "Alt", "Shift": "Shift"}
    modifiers = [aliases[part] for part in parts[:-1] if part in aliases]
    return {"key": key, "modifiers": modifiers}


def _active_stack():
    try:
        return sp.textureset.get_active_stack()
    except Exception as exc:
        raise RuntimeError(_tr("no_active_stack")) from exc


def _selected_node():
    selected = sp.layerstack.get_selected_nodes(_active_stack())
    return selected[0] if selected else None


def _is_layer_node(node):
    return hasattr(node, "has_mask") and hasattr(node, "add_mask")


def _layer_from_node(node):
    current = node
    while current is not None:
        if _is_layer_node(current):
            return current
        if not hasattr(current, "get_parent"):
            return None
        current = current.get_parent()
    return None


def _top_insert_position():
    return sp.layerstack.InsertPosition.from_textureset_stack(_active_stack())


def _effect_insert_position():
    node = _selected_node()
    if node is None:
        fill = _add_fill_layer(select_created=False)
        return sp.layerstack.InsertPosition.inside_node(fill, sp.layerstack.NodeStack.Content)
    if _is_layer_node(node):
        node_stack = sp.layerstack.NodeStack.Content
        try:
            if sp.layerstack.get_selection_type(node) == sp.layerstack.SelectionType.Mask:
                if not node.has_mask():
                    node.add_mask(sp.layerstack.MaskBackground.Black)
                node_stack = sp.layerstack.NodeStack.Mask
        except Exception:
            pass
        return sp.layerstack.InsertPosition.inside_node(node, node_stack)
    return sp.layerstack.InsertPosition.above_node(node)


def _mask_insert_position():
    layer = _layer_from_node(_selected_node())
    if layer is None:
        layer = _add_fill_layer(select_created=False)
    if not layer.has_mask():
        layer.add_mask(sp.layerstack.MaskBackground.Black)
    return sp.layerstack.InsertPosition.inside_node(layer, sp.layerstack.NodeStack.Mask)


def _select_node(node):
    try:
        sp.layerstack.set_selected_nodes([node])
    except Exception:
        _error("Failed to select node:\n" + traceback.format_exc())


def _toggle_selected_visibility():
    node = _selected_node()
    if node is None:
        return
    if not hasattr(node, "is_visible") or not hasattr(node, "set_visible"):
        return
    try:
        node.set_visible(not node.is_visible())
    except Exception:
        _error("Failed to toggle visibility:\n" + traceback.format_exc())


def _selection_type_for_node(node):
    if not _is_layer_node(node):
        return None
    try:
        return sp.layerstack.get_selection_type(node)
    except Exception:
        return sp.layerstack.SelectionType.Content


def _selection_matches(node, selection_type):
    current = _selected_node()
    if current is None or current.uid() != node.uid():
        return False
    if selection_type is None:
        return True
    return _selection_type_for_node(current) == selection_type


def _apply_selection_slot(node, selection_type):
    if selection_type is not None:
        try:
            sp.layerstack.set_selection_type(node, selection_type)
        except Exception:
            pass
    _select_node(node)
    if selection_type is not None:
        try:
            sp.layerstack.set_selection_type(node, selection_type)
        except Exception:
            pass
    return _selection_matches(node, selection_type)


def _add_fill_layer(select_created=True):
    node = sp.layerstack.insert_fill(_top_insert_position())
    if select_created:
        _select_node(node)
    return node


def _add_paint_layer(select_created=True):
    node = sp.layerstack.insert_paint(_top_insert_position())
    if select_created:
        _select_node(node)
    return node


def _add_paint_effect():
    node = sp.layerstack.insert_paint(_effect_insert_position())
    _select_node(node)
    return node


def _add_fill_effect():
    node = sp.layerstack.insert_fill(_effect_insert_position())
    _select_node(node)
    return node


def _action_unavailable(action):
    if action != "add_black_mask":
        return False
    try:
        layer = _layer_from_node(_selected_node())
        return layer is not None and bool(layer.has_mask())
    except Exception:
        return False


def _add_black_mask():
    layer = _layer_from_node(_selected_node())
    if layer is None:
        layer = _add_fill_layer(select_created=False)
    if layer.has_mask():
        return layer
    layer.add_mask(sp.layerstack.MaskBackground.Black)
    _select_node(layer)
    try:
        sp.layerstack.set_selection_type(layer, sp.layerstack.SelectionType.Mask)
    except Exception:
        pass
    return layer


def _add_compare_mask():
    node = sp.layerstack.insert_compare_mask_effect(_mask_insert_position())
    _select_node(node)
    return node


def _add_filter_effect():
    node = sp.layerstack.insert_filter_effect(_effect_insert_position())
    _select_node(node)
    return node


def _next_anchor_point_name():
    base_name = _tr("anchor_point_name")
    existing_names = set()
    try:
        for node, _selection_type in _all_selection_slots():
            if hasattr(node, "get_name"):
                existing_names.add(str(node.get_name()))
    except Exception:
        pass
    if base_name not in existing_names:
        return base_name
    index = 2
    while "%s %d" % (base_name, index) in existing_names:
        index += 1
    return "%s %d" % (base_name, index)


def _add_anchor_point():
    node = sp.layerstack.insert_anchor_point_effect(
        _effect_insert_position(), _next_anchor_point_name())
    _select_node(node)
    return node


def _find_filter_resource(names):
    queries = []
    for name in names:
        queries.extend([
            "s:starterassets u:filter n:%s=" % name,
            "s:starterassets u:filter n:%s" % name,
            "u:filter n:%s=" % name,
            "u:filter n:%s" % name
        ])
    for query in queries:
        try:
            results = sp.resource.search(query)
            if results:
                return results[0].identifier()
        except Exception:
            pass
    raise RuntimeError(_tr("blur_missing"))


def _find_resource_preview(action):
    return ""


def _add_blur_filter():
    resource_id = _find_filter_resource(["Blur", "Blur Slope", "Blur HQ"])
    node = sp.layerstack.insert_filter_effect(_effect_insert_position(), resource_id)
    _select_node(node)
    return node


def _add_filter_resource(resource_url):
    resource_id = sp.resource.ResourceID.from_url(str(resource_url))
    node = sp.layerstack.insert_filter_effect(_effect_insert_position(), resource_id)
    _select_node(node)
    return node


def _add_levels():
    node = sp.layerstack.insert_levels_effect(_effect_insert_position())
    _select_node(node)
    return node


def _add_color_selection():
    node = sp.layerstack.insert_color_selection_effect(_mask_insert_position())
    _select_node(node)
    return node


def _flatten_layer_nodes(layers):
    slots = []
    for layer in layers:
        slots.append((layer, sp.layerstack.SelectionType.Content))
        if hasattr(layer, "has_mask") and layer.has_mask():
            slots.append((layer, sp.layerstack.SelectionType.Mask))
        if hasattr(layer, "content_effects"):
            slots.extend((effect, None) for effect in layer.content_effects())
        if hasattr(layer, "mask_effects"):
            slots.extend((effect, None) for effect in layer.mask_effects())
        if hasattr(layer, "sub_layers"):
            slots.extend(_flatten_layer_nodes(layer.sub_layers()))
    return slots


def _all_selection_slots():
    stack = _active_stack()
    return _flatten_layer_nodes(sp.layerstack.get_root_layer_nodes(stack))


def _flatten_layer_row_nodes(layers):
    slots = []
    for layer in layers:
        if _is_layer_node(layer):
            slots.append((layer, sp.layerstack.SelectionType.Content))
        if hasattr(layer, "sub_layers"):
            slots.extend(_flatten_layer_row_nodes(layer.sub_layers()))
    return slots


def _all_layer_row_slots():
    stack = _active_stack()
    return _flatten_layer_row_nodes(sp.layerstack.get_root_layer_nodes(stack))


def _select_relative_node(step):
    global _LAST_SLOT_INDEX
    try:
        slots = _all_selection_slots()
        if not slots:
            return
        current = _selected_node()
        if current is None:
            index = 0 if step >= 0 else len(slots) - 1
        else:
            current_uid = current.uid()
            current_selection = _selection_type_for_node(current)
            index = None
            if _LAST_SLOT_INDEX is not None and 0 <= _LAST_SLOT_INDEX < len(slots):
                last_node, last_selection = slots[_LAST_SLOT_INDEX]
                if last_node.uid() == current_uid:
                    if last_selection is None or last_selection == current_selection:
                        index = _LAST_SLOT_INDEX
            fallback_index = None
            for idx, slot in enumerate(slots):
                node, selection_type = slot
                if node.uid() == current_uid and fallback_index is None:
                    fallback_index = idx
                if node.uid() == current_uid and selection_type == current_selection:
                    index = idx
                    break
            if index is None:
                index = fallback_index if fallback_index is not None else 0
            index = (index + step) % len(slots)
        attempts = 0
        while attempts < len(slots):
            node, selection_type = slots[index]
            if _apply_selection_slot(node, selection_type):
                _LAST_SLOT_INDEX = index
                return
            index = (index + step) % len(slots)
            attempts += 1
        _LAST_SLOT_INDEX = None
    except Exception:
        _error("Failed to select relative node:\n" + traceback.format_exc())


def _select_relative_layer_row(step):
    global _LAST_LAYER_SLOT_INDEX
    try:
        slots = _all_layer_row_slots()
        if not slots:
            return
        current = _selected_node()
        current_layer = _layer_from_node(current) if current is not None else None
        current_uid = current_layer.uid() if current_layer is not None else None
        if current_uid is None:
            index = 0 if step >= 0 else len(slots) - 1
        else:
            index = None
            if _LAST_LAYER_SLOT_INDEX is not None and 0 <= _LAST_LAYER_SLOT_INDEX < len(slots):
                last_node, _last_selection = slots[_LAST_LAYER_SLOT_INDEX]
                if last_node.uid() == current_uid:
                    index = _LAST_LAYER_SLOT_INDEX
            for idx, slot in enumerate(slots):
                node, _selection_type = slot
                if node.uid() == current_uid:
                    index = idx
                    break
            if index is None:
                index = 0
            index = (index + step) % len(slots)
        attempts = 0
        while attempts < len(slots):
            node, selection_type = slots[index]
            if _apply_selection_slot(node, selection_type):
                _LAST_LAYER_SLOT_INDEX = index
                return
            index = (index + step) % len(slots)
            attempts += 1
        _LAST_LAYER_SLOT_INDEX = None
    except Exception:
        _error("Failed to select relative layer row:\n" + traceback.format_exc())


def _run_action(action):
    try:
        if _action_unavailable(action):
            return
        if action == "add_fill_layer":
            _add_fill_layer()
        elif action == "add_paint_layer":
            _add_paint_layer()
        elif action == "add_paint_effect":
            _add_paint_effect()
        elif action == "add_fill_effect":
            _add_fill_effect()
        elif action == "add_black_mask":
            _add_black_mask()
        elif action == "add_compare_mask":
            _add_compare_mask()
        elif action == "add_filter_effect":
            _add_filter_effect()
        elif action == "add_anchor_point":
            _add_anchor_point()
        elif action == "add_blur_filter":
            _add_blur_filter()
        elif action == "add_levels":
            _add_levels()
        elif action == "add_color_selection":
            _add_color_selection()
        elif action.startswith(FILTER_ACTION_PREFIX):
            _add_filter_resource(action[len(FILTER_ACTION_PREFIX):])
        else:
            raise RuntimeError("Unknown action: %s" % action)
        _log("Executed action: " + action)
    except Exception as exc:
        _error("Action failed: %s\n%s" % (action, traceback.format_exc()))
        QtWidgets.QMessageBox.warning(
            substance_painter.ui.get_main_window(),
            _tr("dialog_title"),
            str(exc))


class ToolButton(QtWidgets.QPushButton):
    hovered = QtCore.Signal(str)

    def __init__(self, item, config, parent=None):
        super().__init__(parent)
        self.item = item
        self.config = config
        self.setObjectName("toolButton")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedSize(int(config.get("card_width", 154)), 40)
        self.setText(_localized_value(item, "labels", config))
        self.setToolTip(_localized_value(item, "descriptions", config))

    def enterEvent(self, event):
        self.hovered.emit(self.item.get("action", ""))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered.emit("")
        super().leaveEvent(event)


class ApplyFeedbackButton(QtWidgets.QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._normal_text = ""
        self._feedback_group = QtCore.QSequentialAnimationGroup(self)

        self._green_in = QtCore.QVariantAnimation(self._feedback_group)
        self._green_in.setDuration(160)
        self._green_in.setStartValue(QtGui.QColor("#2d8fe8"))
        self._green_in.setEndValue(QtGui.QColor("#2faa60"))
        self._green_in.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._green_in.valueChanged.connect(self._set_feedback_color)

        self._green_hold = QtCore.QPauseAnimation(560, self._feedback_group)

        self._green_out = QtCore.QVariantAnimation(self._feedback_group)
        self._green_out.setDuration(300)
        self._green_out.setStartValue(QtGui.QColor("#2faa60"))
        self._green_out.setEndValue(QtGui.QColor("#2d8fe8"))
        self._green_out.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
        self._green_out.valueChanged.connect(self._set_feedback_color)

        self._feedback_group.addAnimation(self._green_in)
        self._feedback_group.addAnimation(self._green_hold)
        self._feedback_group.addAnimation(self._green_out)
        self._feedback_group.finished.connect(self._finish_success_feedback)

    def show_success(self, success_text, normal_text):
        if self._feedback_group.state() != QtCore.QAbstractAnimation.Stopped:
            self._feedback_group.stop()
        self._normal_text = normal_text
        self.setText(success_text)
        self.setEnabled(False)
        self._set_feedback_color(QtGui.QColor("#2d8fe8"))
        self._feedback_group.start()

    def _set_feedback_color(self, color):
        color = QtGui.QColor(color)
        border = color.lighter(116)
        self.setStyleSheet("""
            QPushButton#primaryButton,
            QPushButton#primaryButton:hover,
            QPushButton#primaryButton:pressed,
            QPushButton#primaryButton:disabled {
                background: %s;
                border-color: %s;
                color: white;
            }
        """ % (color.name(), border.name()))

    def _finish_success_feedback(self):
        self.setStyleSheet("")
        self.setText(self._normal_text)
        self.setEnabled(True)


class ShortcutCaptureEdit(QtWidgets.QLineEdit):
    keySequenceChanged = QtCore.Signal(QtGui.QKeySequence)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("shortcutValueEdit")
        self.setReadOnly(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._capture_prompt = ""
        self._capturing = False
        self._sequence = QtGui.QKeySequence()
        self._previous_sequence = QtGui.QKeySequence()

    def set_capture_prompt(self, text):
        self._capture_prompt = str(text or "")
        if self._capturing:
            self.setText(self._capture_prompt)

    def setKeySequence(self, sequence):
        self._set_capturing(False)
        sequence = QtGui.QKeySequence(sequence)
        changed = sequence != self._sequence
        self._sequence = sequence
        self._update_sequence_text()
        if changed:
            self.keySequenceChanged.emit(self.keySequence())

    def keySequence(self):
        return QtGui.QKeySequence(self._sequence)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == QtCore.Qt.LeftButton:
            self._begin_capture()

    def focusOutEvent(self, event):
        if self._capturing:
            self._cancel_capture()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if not self._capturing:
            super().keyPressEvent(event)
            return
        self._capture_key_event(event)

    def eventFilter(self, obj, event):
        del obj
        if not self._capturing:
            return False
        if event.type() == QtCore.QEvent.ApplicationDeactivate:
            self._cancel_capture()
            return False
        if event.type() == QtCore.QEvent.ShortcutOverride:
            event.accept()
            return True
        if event.type() == QtCore.QEvent.KeyPress:
            return self._capture_key_event(event)
        return False

    def hideEvent(self, event):
        if self._capturing:
            self._cancel_capture()
        super().hideEvent(event)

    def _capture_key_event(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self._cancel_capture()
            event.accept()
            return True
        modifier_keys = (
            QtCore.Qt.Key_Control,
            QtCore.Qt.Key_Shift,
            QtCore.Qt.Key_Alt,
            QtCore.Qt.Key_Meta)
        if event.key() in modifier_keys:
            event.accept()
            return True
        try:
            sequence = QtGui.QKeySequence(event.keyCombination())
        except Exception:
            modifiers = getattr(event.modifiers(), "value", event.modifiers())
            key = getattr(event.key(), "value", event.key())
            sequence = QtGui.QKeySequence(int(modifiers) | int(key))
        self._sequence = sequence
        self._set_capturing(False)
        self._update_sequence_text()
        self.keySequenceChanged.emit(self.keySequence())
        event.accept()
        return True

    def _begin_capture(self):
        if self._capturing:
            return
        self._previous_sequence = QtGui.QKeySequence(self._sequence)
        self.setFocus(QtCore.Qt.MouseFocusReason)
        self._set_capturing(True)
        self.setText(self._capture_prompt)
        self.selectAll()

    def _cancel_capture(self):
        self._sequence = QtGui.QKeySequence(self._previous_sequence)
        self._set_capturing(False)
        self._update_sequence_text()

    def _set_capturing(self, capturing):
        capturing = bool(capturing)
        if capturing == self._capturing:
            return
        self._capturing = capturing
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        if capturing:
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)

    def _update_sequence_text(self):
        self.setText(self._sequence.toString(QtGui.QKeySequence.NativeText))


class WheelValueEdit(QtWidgets.QLineEdit):
    valueChanged = QtCore.Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("numericValueEdit")
        self._minimum = 0
        self._maximum = 100
        self._value = 0
        self._suffix = ""
        self._wheel_delta = 0
        self.editingFinished.connect(self._commit_text)

    def setRange(self, minimum, maximum):
        self._minimum = int(minimum)
        self._maximum = max(self._minimum, int(maximum))
        self.setValue(self._value)

    def setMaximum(self, maximum):
        self._maximum = max(self._minimum, int(maximum))
        self.setValue(self._value)

    def setSuffix(self, suffix):
        self._suffix = str(suffix or "")
        self._update_text()

    def setValue(self, value):
        value = max(self._minimum, min(self._maximum, int(value)))
        changed = value != self._value
        self._value = value
        self._update_text()
        if changed:
            self.valueChanged.emit(value)

    def value(self):
        return self._value

    def stepBy(self, steps):
        self.setValue(self._value + int(steps))

    def _update_text(self):
        self.setText("%d%s" % (self._value, self._suffix))

    def _commit_text(self):
        match = re.search(r"-?\d+", self.text())
        if match is None:
            self._update_text()
            return
        self.setValue(int(match.group(0)))

    def focusInEvent(self, event):
        super().focusInEvent(event)
        QtCore.QTimer.singleShot(0, self.selectAll)

    def focusOutEvent(self, event):
        self._wheel_delta = 0
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == QtCore.Qt.LeftButton:
            QtCore.QTimer.singleShot(0, self.selectAll)

    def wheelEvent(self, event):
        if not self.hasFocus():
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.pixelDelta().y()
        if hasattr(event, "inverted") and event.inverted():
            delta = -delta
        self._wheel_delta += delta
        steps = int(self._wheel_delta / 120)
        if steps:
            self._wheel_delta -= steps * 120
            self.stepBy(steps)
            self.selectAll()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Up:
            self.stepBy(1)
            self.selectAll()
            event.accept()
            return
        if event.key() == QtCore.Qt.Key_Down:
            self.stepBy(-1)
            self.selectAll()
            event.accept()
            return
        super().keyPressEvent(event)


class CommandItemDelegate(QtWidgets.QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.data(ROLE_ROW_KIND) == ROW_CATEGORY:
            painter.save()
            font = QtGui.QFont(option.font)
            font.setBold(True)
            painter.setFont(font)
            text = str(index.data(QtCore.Qt.DisplayRole) or "")
            text_rect = option.rect.adjusted(8, 0, -8, 0)
            painter.setPen(QtGui.QColor("#aeb1b4"))
            painter.drawText(
                text_rect,
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                text)
            text_width = QtGui.QFontMetrics(font).horizontalAdvance(text)
            line_start = text_rect.left() + text_width + 12
            if line_start < text_rect.right():
                separator_pen = QtGui.QPen(QtGui.QColor("#484a4c"), 1)
                separator_pen.setCapStyle(QtCore.Qt.FlatCap)
                painter.setPen(separator_pen)
                line_y = option.rect.center().y() + 0.5
                painter.drawLine(
                    QtCore.QPointF(line_start, line_y),
                    QtCore.QPointF(text_rect.right(), line_y))
            painter.restore()
            return
        super().paint(painter, option, index)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.decorationSize = (
            QtCore.QSize(0, 0)
            if index.data(ROLE_ROW_KIND) == ROW_CATEGORY
            else QtCore.QSize(24, 24))

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        minimum = 26 if index.data(ROLE_ROW_KIND) == ROW_CATEGORY else 38
        size.setHeight(max(minimum, size.height()))
        return size


class RadialPreviewWidget(QtWidgets.QWidget):
    segmentSelected = QtCore.Signal(str)
    itemsChanged = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.selected_id = ""
        self._hover_index = -1
        self._dragged_id = ""
        self._drag_source_index = -1
        self._drag_target_index = -1
        self._drag_delete = False
        self._drag_position = QtCore.QPointF()
        self.setMinimumSize(300, 260)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setMouseTracking(True)

    def set_config(self, config):
        self.config = copy.deepcopy(config)
        self.update()

    def set_selected_id(self, item_id):
        self.selected_id = str(item_id or "")
        self.update()

    def _items(self):
        return [item for item in self.config.get("items", []) if item.get("enabled", True)]

    @staticmethod
    def _point(center, radius, angle_degrees):
        angle = math.radians(angle_degrees)
        return QtCore.QPointF(
            center.x() + math.cos(angle) * radius,
            center.y() + math.sin(angle) * radius)

    def _segment_path(self, center, outer_radius, inner_radius, start, end):
        sweep = end - start
        outer_rect = QtCore.QRectF(
            center.x() - outer_radius,
            center.y() - outer_radius,
            outer_radius * 2,
            outer_radius * 2)
        inner_rect = QtCore.QRectF(
            center.x() - inner_radius,
            center.y() - inner_radius,
            inner_radius * 2,
            inner_radius * 2)
        path = QtGui.QPainterPath()
        path.arcMoveTo(outer_rect, -start)
        path.arcTo(outer_rect, -start, -sweep)
        path.lineTo(self._point(center, inner_radius, end))
        path.arcTo(inner_rect, -end, sweep)
        path.closeSubpath()
        return path

    def _arc_path(self, center, radius, start, end):
        arc_rect = QtCore.QRectF(
            center.x() - radius,
            center.y() - radius,
            radius * 2,
            radius * 2)
        path = QtGui.QPainterPath()
        path.arcMoveTo(arc_rect, -start)
        path.arcTo(arc_rect, -start, -(end - start))
        return path

    def _wheel_geometry(self):
        center = QtCore.QPointF(self.width() * 0.5, self.height() * 0.5)
        configured_outer = max(1.0, float(self.config.get("wheel_radius", 154)))
        available_outer = min(
            max(74.0, (self.width() - 150.0) * 0.5),
            max(74.0, (self.height() - 110.0) * 0.5))
        outer_radius = min(configured_outer, available_outer)
        inner_ratio = float(self.config.get("inner_radius", 76)) / configured_outer
        inner_radius = outer_radius * max(0.28, min(0.72, inner_ratio))
        return center, outer_radius, inner_radius

    def _segment_index_at(self, point):
        items = self._items()
        if not items:
            return -1
        center, outer_radius, inner_radius = self._wheel_geometry()
        dx = point.x() - center.x()
        dy = point.y() - center.y()
        distance = math.sqrt(dx * dx + dy * dy)
        if distance < inner_radius or distance > outer_radius:
            return -1
        span = 360.0 / len(items)
        angle = math.degrees(math.atan2(dy, dx))
        index = int(((angle - (-90.0 - span * 0.5)) + 360.0) % 360.0 / span)
        return min(len(items) - 1, index)

    def paintEvent(self, event):
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QtGui.QColor("#303030"))

        grid_pen = QtGui.QPen(QtGui.QColor("#393939"), 1)
        painter.setPen(grid_pen)
        for x in range(0, self.width(), 80):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 80):
            painter.drawLine(0, y, self.width(), y)

        items = self._items()
        center, outer_radius, inner_radius = self._wheel_geometry()
        count = max(1, len(items))
        span = 360.0 / count
        highlight = QtGui.QColor(str(self.config.get("highlight_color", "#2d8fe8")))

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 48))
        painter.drawEllipse(center, outer_radius + 8, outer_radius + 8)

        hover_highlight = None
        drag_target_highlight = None
        for index in range(count):
            item = items[index] if index < len(items) else None
            center_angle = -90.0 + span * index
            start = center_angle - span * 0.5
            end = center_angle + span * 0.5
            hovered = not self._dragged_id and index == self._hover_index
            drag_target = (
                bool(self._dragged_id) and not self._drag_delete
                and index == self._drag_target_index)
            path = self._segment_path(center, outer_radius, inner_radius, start, end)
            if drag_target:
                drag_fill = QtGui.QColor("#49b653")
                drag_fill.setAlpha(92)
                painter.setBrush(drag_fill)
                painter.setPen(QtGui.QPen(QtGui.QColor("#70d478"), 1))
                drag_target_highlight = (start, end)
            else:
                painter.setBrush(QtGui.QColor(
                    "#343638" if hovered else "#272727"))
                painter.setPen(QtGui.QPen(
                    QtGui.QColor(
                        "#5b5e61" if hovered else "#484848"), 1))
            painter.drawPath(path)

            if hovered:
                hover_highlight = (start, end)

            if item is None:
                continue
            icon_radius = (outer_radius + inner_radius) * 0.5
            icon_center = self._point(center, icon_radius, center_angle)
            pixmap = _item_icon_pixmap(item)
            if not pixmap.isNull():
                painter.setOpacity(
                    1.0 if hovered or drag_target else 0.72)
                icon_size = max(18.0, min(24.0, outer_radius * 0.15))
                target = QtCore.QRectF(
                    icon_center.x() - icon_size * 0.5,
                    icon_center.y() - icon_size * 0.5,
                    icon_size, icon_size)
                painter.drawPixmap(target, pixmap, QtCore.QRectF(pixmap.rect()))
                painter.setOpacity(1.0)
            line_start = self._point(center, outer_radius + 7, center_angle)
            line_end = self._point(center, outer_radius + 20, center_angle)
            guide_pen = QtGui.QPen(QtGui.QColor("#626568"), 1.35)
            guide_pen.setCapStyle(QtCore.Qt.RoundCap)
            painter.setPen(guide_pen)
            painter.drawLine(line_start, line_end)
            label_width = min(136.0, max(96.0, self.width() * 0.2))
            label_height = 24.0
            label_gap = 8.0
            direction_x = math.cos(math.radians(center_angle))
            direction_y = math.sin(math.radians(center_angle))
            if abs(direction_x) < 0.28:
                label_left = line_end.x() - label_width * 0.5
                label_top = (
                    line_end.y() - label_gap - label_height
                    if direction_y < 0
                    else line_end.y() + label_gap)
                label_alignment = QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter
            elif direction_x > 0:
                label_left = line_end.x() + label_gap
                label_top = line_end.y() - label_height * 0.5
                label_alignment = QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
            else:
                label_left = line_end.x() - label_gap - label_width
                label_top = line_end.y() - label_height * 0.5
                label_alignment = QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
            label_left = max(
                8.0, min(label_left, self.width() - label_width - 8.0))
            label_top = max(
                4.0, min(label_top, self.height() - label_height - 4.0))
            label_rect = QtCore.QRectF(
                label_left, label_top, label_width, label_height)
            painter.setPen(QtGui.QColor(
                "#f0f0f0" if hovered else "#aeb1b4"))
            label_font = painter.font()
            label_font.setPixelSize(12)
            label_font.setWeight(QtGui.QFont.Medium)
            painter.setFont(label_font)
            label_text = _localized_value(item, "labels", self.config)
            label_text = QtGui.QFontMetrics(label_font).elidedText(
                label_text, QtCore.Qt.ElideRight, int(label_width))
            painter.drawText(label_rect, label_alignment, label_text)

        painter.setBrush(QtCore.Qt.NoBrush)
        ring_radius = outer_radius - 3
        painter.setPen(QtGui.QPen(QtGui.QColor("#171717"), 7))
        painter.drawEllipse(center, ring_radius, ring_radius)

        active_highlight = None if self._drag_delete else (
            drag_target_highlight or hover_highlight)
        if active_highlight is not None:
            start, end = active_highlight
            active_color = QtGui.QColor(
                "#70d478" if drag_target_highlight is not None else highlight)
            glow_color = QtGui.QColor(active_color)
            glow_color.setAlpha(28)
            glow_pen = QtGui.QPen(glow_color, 10)
            glow_pen.setCapStyle(QtCore.Qt.FlatCap)
            painter.setPen(glow_pen)
            highlight_path = self._arc_path(center, ring_radius, start, end)
            painter.drawPath(highlight_path)
            highlight_pen = QtGui.QPen(active_color, 4)
            highlight_pen.setCapStyle(QtCore.Qt.FlatCap)
            painter.setPen(highlight_pen)
            painter.drawPath(highlight_path)

        painter.setPen(QtGui.QPen(QtGui.QColor("#505050"), 1.5))
        painter.setBrush(QtGui.QColor("#202020"))
        painter.drawEllipse(center, inner_radius, inner_radius)

        if self._drag_delete:
            delete_radius = ring_radius
            for width, alpha in ((26, 24), (18, 42), (10, 90)):
                painter.setPen(QtGui.QPen(QtGui.QColor(255, 66, 54, alpha), width))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(center, delete_radius, delete_radius)
            painter.setPen(QtGui.QPen(QtGui.QColor("#ff493d"), 3))
            painter.setBrush(QtGui.QColor(145, 48, 40, 150))
            painter.drawEllipse(self._drag_position, 25, 25)

        hovered_item = (
            items[self._hover_index]
            if not self._dragged_id and 0 <= self._hover_index < len(items)
            else None)
        display_item = hovered_item
        if self._drag_delete:
            label = _tr("release_to_remove", self.config)
            label_color = QtGui.QColor("#ff5a4f")
        elif self._dragged_id and self._drag_target_index >= 0:
            label = _tr("move_to_position", self.config).format(
                position=self._drag_target_index + 1)
            label_color = QtGui.QColor("#55cf62")
        else:
            selected_item = next((
                item for item in items
                if str(item.get("id", "")) == self.selected_id), None)
            display_item = display_item or selected_item
            label = (
                _localized_value(display_item, "labels", self.config)
                if display_item is not None
                else _active_menu_name(self.config))
            label_color = QtGui.QColor(
                "#f2f2f2" if display_item is not None else "#b8b8b8")
        painter.setPen(label_color)
        font = painter.font()
        font.setPixelSize(14)
        font.setBold(display_item is not None)
        painter.setFont(font)
        painter.drawText(
            QtCore.QRectF(
                center.x() - inner_radius * 0.78,
                center.y() - 24,
                inner_radius * 1.56,
                48),
            QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
            label)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return
        items = self._items()
        if not items:
            return
        index = self._segment_index_at(event.position())
        if index < 0:
            return
        self._drag_source_index = index
        self._drag_target_index = index
        self._dragged_id = str(items[index].get("id", ""))
        self._drag_position = event.position()
        self._drag_delete = False
        self._hover_index = -1
        self.selected_id = self._dragged_id
        self.setCursor(QtCore.Qt.ClosedHandCursor)
        self.segmentSelected.emit(self._dragged_id)
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragged_id:
            hover_index = self._segment_index_at(event.position())
            if hover_index != self._hover_index:
                self._hover_index = hover_index
                self.update()
            event.accept()
            return
        self._drag_position = event.position()
        center, outer_radius, inner_radius = self._wheel_geometry()
        dx = self._drag_position.x() - center.x()
        dy = self._drag_position.y() - center.y()
        distance = math.sqrt(dx * dx + dy * dy)
        self._drag_delete = distance > outer_radius + 18
        self.setCursor(
            QtCore.Qt.ForbiddenCursor if self._drag_delete
            else QtCore.Qt.ClosedHandCursor)
        self._drag_target_index = -1 if self._drag_delete else self._segment_index_at(
            self._drag_position)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton or not self._dragged_id:
            return
        items = list(self.config.get("items", []))
        active = [item for item in items if item.get("enabled", True)]
        changed = False
        source = next(
            (item for item in active if str(item.get("id", "")) == self._dragged_id),
            None)
        if source is not None and self._drag_delete:
            source["enabled"] = False
            changed = True
        elif source is not None and self._drag_target_index >= 0:
            source_index = active.index(source)
            target_index = min(self._drag_target_index, len(active) - 1)
            if source_index != target_index:
                active.pop(source_index)
                active.insert(min(target_index, len(active)), source)
                inactive = [item for item in items if not item.get("enabled", True)]
                self.config["items"] = active + inactive
                changed = True

        self._dragged_id = ""
        self._drag_source_index = -1
        self._drag_target_index = -1
        self._drag_delete = False
        self._hover_index = self._segment_index_at(event.position())
        self.unsetCursor()
        if changed:
            self.itemsChanged.emit(copy.deepcopy(self.config.get("items", [])))
        self.update()
        event.accept()

    def leaveEvent(self, event):
        if not self._dragged_id and self._hover_index != -1:
            self._hover_index = -1
            self.update()
        super().leaveEvent(event)


class MenuPresetCard(QtWidgets.QFrame):
    activated = QtCore.Signal(str)
    renameRequested = QtCore.Signal(str)
    deleteRequested = QtCore.Signal(str)

    def __init__(self, menu_id, parent=None):
        super().__init__(parent)
        self.menu_id = str(menu_id)
        self.setObjectName("menuPresetCard")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setMinimumHeight(74)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 8, 9)
        layout.setSpacing(3)
        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)
        self.name_label = QtWidgets.QLabel(self)
        self.name_label.setObjectName("menuPresetName")
        top_row.addWidget(self.name_label, 1)

        edit_icon = QtGui.QIcon(_icon_path("edit"))
        delete_icon = QtGui.QIcon(_icon_path("delete"))

        self.rename_button = QtWidgets.QToolButton(self)
        self.rename_button.setObjectName("presetActionButton")
        self.rename_button.setIcon(edit_icon)
        self.rename_button.setIconSize(QtCore.QSize(18, 18))
        self.rename_button.setFixedSize(28, 28)
        self.rename_button.clicked.connect(
            lambda checked=False: self.renameRequested.emit(self.menu_id))
        top_row.addWidget(self.rename_button)
        self.delete_button = QtWidgets.QToolButton(self)
        self.delete_button.setObjectName("presetActionButton")
        self.delete_button.setIcon(delete_icon)
        self.delete_button.setIconSize(QtCore.QSize(18, 18))
        self.delete_button.setFixedSize(28, 28)
        self.delete_button.clicked.connect(
            lambda checked=False: self.deleteRequested.emit(self.menu_id))
        top_row.addWidget(self.delete_button)
        layout.addLayout(top_row)

        self.count_label = QtWidgets.QLabel(self)
        self.count_label.setObjectName("menuPresetCount")
        layout.addWidget(self.count_label)

    def set_data(self, name, count_text, selected, can_delete, config):
        self.name_label.setText(str(name))
        self.count_label.setText(str(count_text))
        self.rename_button.setToolTip(_tr("rename_menu", config))
        self.delete_button.setToolTip(_tr("delete_menu", config))
        self.delete_button.setVisible(bool(can_delete))
        self.setProperty("selected", bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.activated.emit(self.menu_id)
            event.accept()
            return
        super().mousePressEvent(event)


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._highlight_color = QtGui.QColor("#2d8fe8")
        self._update_manager = None
        self.shortcut_assignment_rows = {}
        self.working_config = copy.deepcopy(DEFAULT_CONFIG)
        self.setWindowTitle(_tr("settings_title"))
        self.setModal(False)
        self.resize(1120, 720)
        self.setMinimumSize(720, 420)
        self.setObjectName("radialSettingsDialog")
        self._build_ui()
        self._thumbnail_queue = []
        self._thumbnail_timer = QtCore.QTimer(self)
        self._thumbnail_timer.setSingleShot(True)
        self._thumbnail_timer.timeout.connect(self._load_next_thumbnail)
        self.load_config(_load_config())
        self._bind_update_manager()

    def _build_ui(self):
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        root_layout.addWidget(splitter, 1)

        sidebar = QtWidgets.QFrame(splitter)
        sidebar.setObjectName("settingsSidebar")
        sidebar.setMinimumWidth(280)
        sidebar.setMaximumWidth(370)
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        self.sidebar_stack = QtWidgets.QStackedWidget(sidebar)
        self.sidebar_stack.setObjectName("sidebarStack")
        sidebar_layout.addWidget(self.sidebar_stack)

        self.command_page = QtWidgets.QWidget(self.sidebar_stack)
        command_layout = QtWidgets.QVBoxLayout(self.command_page)
        command_layout.setContentsMargins(18, 18, 18, 18)
        command_layout.setSpacing(10)

        search_row = QtWidgets.QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(8)
        search_row.setAlignment(QtCore.Qt.AlignVCenter)
        self.search_edit = QtWidgets.QLineEdit(self.command_page)
        self.search_edit.setPlaceholderText(_tr("search_commands"))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedHeight(38)
        self.search_edit.textChanged.connect(self._filter_items)
        search_row.addWidget(self.search_edit, 1, QtCore.Qt.AlignVCenter)
        self.properties_button = QtWidgets.QToolButton(self.command_page)
        self.properties_button.setObjectName("propertiesButton")
        self.properties_button.setIcon(QtGui.QIcon(_icon_pixmap("settings")))
        self.properties_button.setIconSize(QtCore.QSize(20, 20))
        self.properties_button.setFixedSize(38, 38)
        self.properties_button.setFocusPolicy(QtCore.Qt.NoFocus)
        self.properties_button.clicked.connect(self._show_properties_page)
        search_row.addWidget(self.properties_button, 0, QtCore.Qt.AlignVCenter)
        command_layout.addLayout(search_row)

        self.mode_tab_bar = QtWidgets.QFrame(self.command_page)
        self.mode_tab_bar.setObjectName("modeTabBar")
        mode_tab_layout = QtWidgets.QHBoxLayout(self.mode_tab_bar)
        mode_tab_layout.setContentsMargins(0, 0, 0, 0)
        mode_tab_layout.setSpacing(0)
        self.mode_tab_group = QtWidgets.QButtonGroup(self.mode_tab_bar)
        self.mode_tab_group.setExclusive(True)

        command_tab_container = QtWidgets.QWidget(self.mode_tab_bar)
        command_tab_layout = QtWidgets.QVBoxLayout(command_tab_container)
        command_tab_layout.setContentsMargins(0, 0, 0, 0)
        command_tab_layout.setSpacing(0)
        self.command_tab_button = QtWidgets.QToolButton(command_tab_container)
        self.command_tab_button.setObjectName("modeTab")
        self.command_tab_button.setCheckable(True)
        self.command_tab_button.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.command_tab_button.clicked.connect(
            lambda checked=False: self._set_command_mode(0))
        command_tab_layout.addWidget(self.command_tab_button)
        self.command_tab_indicator = QtWidgets.QFrame(command_tab_container)
        self.command_tab_indicator.setObjectName("modeTabIndicator")
        self.command_tab_indicator.setFixedHeight(2)
        command_tab_layout.addWidget(self.command_tab_indicator)
        mode_tab_layout.addWidget(command_tab_container, 1)

        menu_tab_container = QtWidgets.QWidget(self.mode_tab_bar)
        menu_tab_layout = QtWidgets.QVBoxLayout(menu_tab_container)
        menu_tab_layout.setContentsMargins(0, 0, 0, 0)
        menu_tab_layout.setSpacing(0)
        self.combination_tab_button = QtWidgets.QToolButton(menu_tab_container)
        self.combination_tab_button.setObjectName("modeTab")
        self.combination_tab_button.setCheckable(True)
        self.combination_tab_button.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.combination_tab_button.clicked.connect(
            lambda checked=False: self._set_command_mode(1))
        menu_tab_layout.addWidget(self.combination_tab_button)
        self.combination_tab_indicator = QtWidgets.QFrame(menu_tab_container)
        self.combination_tab_indicator.setObjectName("modeTabIndicator")
        self.combination_tab_indicator.setFixedHeight(2)
        menu_tab_layout.addWidget(self.combination_tab_indicator)
        mode_tab_layout.addWidget(menu_tab_container, 1)

        shortcut_tab_container = QtWidgets.QWidget(self.mode_tab_bar)
        shortcut_tab_layout = QtWidgets.QVBoxLayout(shortcut_tab_container)
        shortcut_tab_layout.setContentsMargins(0, 0, 0, 0)
        shortcut_tab_layout.setSpacing(0)
        self.shortcut_tab_button = QtWidgets.QToolButton(shortcut_tab_container)
        self.shortcut_tab_button.setObjectName("modeTab")
        self.shortcut_tab_button.setCheckable(True)
        self.shortcut_tab_button.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.shortcut_tab_button.clicked.connect(
            lambda checked=False: self._set_command_mode(2))
        shortcut_tab_layout.addWidget(self.shortcut_tab_button)
        self.shortcut_tab_indicator = QtWidgets.QFrame(shortcut_tab_container)
        self.shortcut_tab_indicator.setObjectName("modeTabIndicator")
        self.shortcut_tab_indicator.setFixedHeight(2)
        shortcut_tab_layout.addWidget(self.shortcut_tab_indicator)
        mode_tab_layout.addWidget(shortcut_tab_container, 1)
        self.mode_tab_group.addButton(self.command_tab_button, 0)
        self.mode_tab_group.addButton(self.combination_tab_button, 1)
        self.mode_tab_group.addButton(self.shortcut_tab_button, 2)
        command_layout.addWidget(self.mode_tab_bar)

        self.command_content_stack = QtWidgets.QStackedWidget(self.command_page)
        self.command_content_stack.setObjectName("commandContentStack")
        self.command_list = QtWidgets.QListWidget(self.command_content_stack)
        self.command_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.command_list.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        self.command_list.setIconSize(QtCore.QSize(20, 20))
        self.command_list.setSpacing(0)
        self.command_list.setUniformItemSizes(False)
        self.command_list.setMouseTracking(True)
        self.command_list.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding)
        self.command_list.setItemDelegate(CommandItemDelegate(self.command_list))
        self.command_list.itemSelectionChanged.connect(self._selection_changed)
        self.command_list.itemDoubleClicked.connect(self._restore_command)
        self.command_content_stack.addWidget(self.command_list)
        self.combination_page = QtWidgets.QWidget(self.command_content_stack)
        self.combination_page.setObjectName("combinationPage")
        combination_layout = QtWidgets.QVBoxLayout(self.combination_page)
        combination_layout.setContentsMargins(0, 2, 0, 0)
        combination_layout.setSpacing(8)
        self.new_menu_button = QtWidgets.QPushButton(self.combination_page)
        self.new_menu_button.setObjectName("newMenuButton")
        self.new_menu_button.clicked.connect(self._create_menu_preset)
        combination_layout.addWidget(self.new_menu_button)
        self.menu_scroll = QtWidgets.QScrollArea(self.combination_page)
        self.menu_scroll.setObjectName("menuPresetScroll")
        self.menu_scroll.setWidgetResizable(True)
        self.menu_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.menu_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.menu_cards_host = QtWidgets.QWidget(self.menu_scroll)
        self.menu_cards_host.setObjectName("menuCardsHost")
        self.menu_cards_layout = QtWidgets.QVBoxLayout(self.menu_cards_host)
        self.menu_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.menu_cards_layout.setSpacing(8)
        self.menu_scroll.setWidget(self.menu_cards_host)
        combination_layout.addWidget(self.menu_scroll, 1)
        self.command_content_stack.addWidget(self.combination_page)

        self.shortcut_command_list = QtWidgets.QListWidget(
            self.command_content_stack)
        self.shortcut_command_list.setObjectName("shortcutCommandList")
        self.shortcut_command_list.setSelectionMode(
            QtWidgets.QAbstractItemView.SingleSelection)
        self.shortcut_command_list.setDragDropMode(
            QtWidgets.QAbstractItemView.NoDragDrop)
        self.shortcut_command_list.setIconSize(QtCore.QSize(20, 20))
        self.shortcut_command_list.setSpacing(0)
        self.shortcut_command_list.setUniformItemSizes(False)
        self.shortcut_command_list.setMouseTracking(True)
        self.shortcut_command_list.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding)
        self.shortcut_command_list.setItemDelegate(
            CommandItemDelegate(self.shortcut_command_list))
        self.shortcut_command_list.itemSelectionChanged.connect(
            self._update_status)
        self.shortcut_command_list.itemDoubleClicked.connect(
            self._add_shortcut_from_list_item)
        self.command_content_stack.addWidget(self.shortcut_command_list)
        self.command_content_stack.setCurrentWidget(self.command_list)
        command_layout.addWidget(self.command_content_stack, 1)
        self.sidebar_stack.addWidget(self.command_page)

        self.properties_page = QtWidgets.QWidget(self.sidebar_stack)
        properties_layout = QtWidgets.QVBoxLayout(self.properties_page)
        properties_layout.setContentsMargins(18, 18, 18, 18)
        properties_layout.setSpacing(14)

        properties_header = QtWidgets.QHBoxLayout()
        properties_header.setContentsMargins(0, 0, 0, 4)
        properties_header.setSpacing(8)
        self.back_button = QtWidgets.QToolButton(self.properties_page)
        self.back_button.setObjectName("backButton")
        self.back_button.setIcon(QtGui.QIcon(_icon_pixmap("back")))
        self.back_button.setIconSize(QtCore.QSize(20, 20))
        self.back_button.setFixedSize(32, 32)
        self.back_button.clicked.connect(self._show_commands_page)
        properties_header.addWidget(self.back_button)
        self.appearance_title = QtWidgets.QLabel(
            _tr("wheel_properties"), self.properties_page)
        self.appearance_title.setObjectName("sectionTitle")
        properties_header.addWidget(self.appearance_title)
        properties_header.addStretch(1)
        self.reset_button = QtWidgets.QToolButton(self.properties_page)
        self.reset_button.setObjectName("toolbarButton")
        self.reset_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.reset_button.setIcon(QtGui.QIcon(_icon_pixmap("reset")))
        self.reset_button.setIconSize(QtCore.QSize(18, 18))
        self.reset_button.setFocusPolicy(QtCore.Qt.NoFocus)
        self.reset_button.clicked.connect(self._reset_defaults)
        properties_header.addWidget(self.reset_button)
        properties_layout.addLayout(properties_header)

        self.form = QtWidgets.QFormLayout()
        self.form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        self.form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.form.setHorizontalSpacing(14)
        self.form.setVerticalSpacing(14)
        self.form_labels = {}

        def add_form_row(key, widget):
            label = QtWidgets.QLabel(_tr(key), self.properties_page)
            self.form_labels[key] = label
            self.form.addRow(label, widget)

        def add_resettable_row(key, widget, callback):
            container = QtWidgets.QWidget(self.properties_page)
            container.setObjectName("resettableField")
            container.setFixedHeight(42)
            layout = QtWidgets.QHBoxLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            layout.addWidget(widget, 1, QtCore.Qt.AlignVCenter)
            reset_button = QtWidgets.QToolButton(container)
            reset_button.setObjectName("fieldResetButton")
            reset_button.setIcon(QtGui.QIcon(_icon_pixmap("reset")))
            reset_button.setIconSize(QtCore.QSize(16, 16))
            reset_button.setFixedSize(36, 36)
            reset_button.setFocusPolicy(QtCore.Qt.NoFocus)
            reset_button.setAutoRaise(True)
            reset_button.clicked.connect(callback)
            layout.addWidget(reset_button, 0, QtCore.Qt.AlignVCenter)
            add_form_row(key, container)
            return reset_button

        self.language_combo = QtWidgets.QComboBox(self.properties_page)
        self.language_combo.addItem(_tr("language_painter"), "painter")
        self.language_combo.addItem(_tr("language_zh"), "zh_CN")
        self.language_combo.addItem(_tr("language_en"), "en")
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        add_form_row("language", self.language_combo)
        self.shortcut_edit = ShortcutCaptureEdit(self.properties_page)
        self.shortcut_edit.keySequenceChanged.connect(self._controls_changed)
        self.shortcut_reset_button = add_resettable_row(
            "shortcut", self.shortcut_edit, self._reset_shortcut)
        self.wheel_radius_spin = WheelValueEdit(self.properties_page)
        self.wheel_radius_spin.setRange(120, 210)
        self.wheel_radius_spin.setSuffix(" px")
        self.wheel_radius_spin.setAlignment(QtCore.Qt.AlignRight)
        self.wheel_radius_spin.valueChanged.connect(self._wheel_radius_changed)
        self.wheel_radius_reset_button = add_resettable_row(
            "wheel_radius", self.wheel_radius_spin, self._reset_wheel_radius)
        self.inner_radius_spin = WheelValueEdit(self.properties_page)
        self.inner_radius_spin.setRange(45, 120)
        self.inner_radius_spin.setSuffix(" px")
        self.inner_radius_spin.setAlignment(QtCore.Qt.AlignRight)
        self.inner_radius_spin.valueChanged.connect(self._controls_changed)
        self.inner_radius_reset_button = add_resettable_row(
            "inner_radius", self.inner_radius_spin, self._reset_inner_radius)
        self.color_button = QtWidgets.QPushButton(self.properties_page)
        self.color_button.setFixedHeight(34)
        self.color_button.setToolTip(_tr("highlight_color"))
        self.color_button.clicked.connect(self._choose_highlight_color)
        add_form_row("highlight_color", self.color_button)
        properties_layout.addLayout(self.form)
        properties_layout.addStretch(1)

        self.repository_button = QtWidgets.QToolButton(self.properties_page)
        self.repository_button.setObjectName("repositoryButton")
        self.repository_button.setToolButtonStyle(
            QtCore.Qt.ToolButtonTextBesideIcon)
        self.repository_button.setIcon(QtGui.QIcon(_icon_pixmap("github")))
        self.repository_button.setIconSize(QtCore.QSize(18, 18))
        self.repository_button.setText("Linbainuo")
        self.repository_button.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.repository_button.setFixedHeight(36)
        self.repository_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.repository_button.setFocusPolicy(QtCore.Qt.NoFocus)
        self.repository_button.clicked.connect(self._open_repository)
        properties_layout.addWidget(self.repository_button)

        self.update_section = QtWidgets.QFrame(self.properties_page)
        self.update_section.setObjectName("updateSection")
        update_layout = QtWidgets.QVBoxLayout(self.update_section)
        update_layout.setContentsMargins(0, 12, 0, 0)
        update_layout.setSpacing(7)

        update_header = QtWidgets.QHBoxLayout()
        update_header.setContentsMargins(0, 0, 0, 0)
        update_header.setSpacing(8)
        self.update_title_label = QtWidgets.QLabel(
            _tr("updates"), self.update_section)
        self.update_title_label.setObjectName("updateTitleLabel")
        update_header.addWidget(self.update_title_label)
        update_header.addStretch(1)
        self.version_label = QtWidgets.QLabel(
            "V" + PLUGIN_VERSION, self.update_section)
        self.version_label.setObjectName("versionLabel")
        self.version_label.setAlignment(
            QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        update_header.addWidget(self.version_label)
        update_layout.addLayout(update_header)

        self.update_status_label = QtWidgets.QLabel(self.update_section)
        self.update_status_label.setObjectName("updateStatusLabel")
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setTextInteractionFlags(
            QtCore.Qt.TextSelectableByMouse)
        update_layout.addWidget(self.update_status_label)

        self.update_progress = QtWidgets.QProgressBar(self.update_section)
        self.update_progress.setObjectName("updateProgress")
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.setTextVisible(False)
        self.update_progress.setFixedHeight(3)
        self.update_progress.hide()
        update_layout.addWidget(self.update_progress)

        update_actions = QtWidgets.QHBoxLayout()
        update_actions.setContentsMargins(0, 0, 0, 0)
        update_actions.setSpacing(8)
        self.auto_update_checkbox = QtWidgets.QCheckBox(
            _tr("auto_check_updates"), self.update_section)
        self.auto_update_checkbox.toggled.connect(self._controls_changed)
        update_actions.addWidget(self.auto_update_checkbox, 1)
        self.update_button = QtWidgets.QPushButton(
            _tr("check_updates"), self.update_section)
        self.update_button.setObjectName("updateButton")
        self.update_button.setMinimumWidth(108)
        self.update_button.clicked.connect(self._update_button_clicked)
        update_actions.addWidget(self.update_button)
        update_layout.addLayout(update_actions)
        properties_layout.addWidget(self.update_section)
        self.sidebar_stack.addWidget(self.properties_page)
        self.sidebar_stack.setCurrentWidget(self.command_page)
        self._set_command_mode(0)

        editor = QtWidgets.QFrame(splitter)
        editor.setObjectName("settingsEditor")
        editor_layout = QtWidgets.QVBoxLayout(editor)
        editor_layout.setContentsMargins(18, 14, 18, 14)
        editor_layout.setSpacing(12)

        self.editor_content_stack = QtWidgets.QStackedWidget(editor)
        self.editor_content_stack.setObjectName("editorContentStack")

        self.preview = RadialPreviewWidget(self.editor_content_stack)
        self.preview.segmentSelected.connect(self._select_item_by_id)
        self.preview.itemsChanged.connect(self._preview_items_changed)
        self.editor_content_stack.addWidget(self.preview)

        self.shortcut_editor_page = QtWidgets.QWidget(self.editor_content_stack)
        self.shortcut_editor_page.setObjectName("shortcutEditorPage")
        shortcut_editor_layout = QtWidgets.QVBoxLayout(self.shortcut_editor_page)
        shortcut_editor_layout.setContentsMargins(0, 0, 0, 0)
        shortcut_editor_layout.setSpacing(0)

        self.shortcut_empty_label = QtWidgets.QLabel(self.shortcut_editor_page)
        self.shortcut_empty_label.setObjectName("shortcutEmptyLabel")
        self.shortcut_empty_label.setAlignment(QtCore.Qt.AlignCenter)
        shortcut_editor_layout.addWidget(self.shortcut_empty_label, 1)

        self.shortcut_assignment_scroll = QtWidgets.QScrollArea(
            self.shortcut_editor_page)
        self.shortcut_assignment_scroll.setObjectName("shortcutAssignmentScroll")
        self.shortcut_assignment_scroll.setWidgetResizable(True)
        self.shortcut_assignment_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.shortcut_assignment_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarAlwaysOff)
        self.shortcut_assignment_host = QtWidgets.QWidget(
            self.shortcut_assignment_scroll)
        self.shortcut_assignment_host.setObjectName("shortcutAssignmentHost")
        self.shortcut_assignment_layout = QtWidgets.QVBoxLayout(
            self.shortcut_assignment_host)
        self.shortcut_assignment_layout.setContentsMargins(0, 0, 0, 0)
        self.shortcut_assignment_layout.setSpacing(1)
        self.shortcut_assignment_layout.setAlignment(QtCore.Qt.AlignTop)
        self.shortcut_assignment_scroll.setWidget(self.shortcut_assignment_host)
        shortcut_editor_layout.addWidget(self.shortcut_assignment_scroll, 1)

        self.editor_content_stack.addWidget(self.shortcut_editor_page)
        self.editor_content_stack.setCurrentWidget(self.preview)
        editor_layout.addWidget(self.editor_content_stack, 1)

        footer = QtWidgets.QFrame(editor)
        footer.setObjectName("editorFooter")
        footer.setFixedHeight(64)
        footer.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        footer_layout = QtWidgets.QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 8, 0, 0)
        footer_layout.setSpacing(10)
        status_layout = QtWidgets.QVBoxLayout()
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(2)
        status_layout.setAlignment(QtCore.Qt.AlignVCenter)
        self.status_label = QtWidgets.QLabel(footer)
        self.status_label.setObjectName("statusLabel")
        self.status_label.setMinimumHeight(20)
        self.status_label.setAlignment(
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.hint_label = QtWidgets.QLabel(footer)
        self.hint_label.setObjectName("hintLabel")
        self.hint_label.setMinimumHeight(18)
        self.hint_label.setAlignment(
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.hint_label)
        footer_layout.addLayout(status_layout, 1)

        self.close_button = QtWidgets.QPushButton(_tr("close"), footer)
        self.close_button.setFixedWidth(104)
        self.close_button.clicked.connect(self.close)
        footer_layout.addWidget(self.close_button, 0, QtCore.Qt.AlignVCenter)
        self.apply_button = ApplyFeedbackButton(footer)
        self.apply_button.setText(_tr("apply"))
        self.apply_button.setObjectName("primaryButton")
        self.apply_button.setFixedWidth(116)
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._apply)
        footer_layout.addWidget(self.apply_button, 0, QtCore.Qt.AlignVCenter)
        editor_layout.addWidget(footer)

        splitter.addWidget(sidebar)
        splitter.addWidget(editor)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 780])

        self.setStyleSheet("""
            QDialog#radialSettingsDialog { background: #303030; color: #e3e3e3; }
            QLabel { color: #e3e3e3; }
            QLabel#sectionTitle { color: #f0f0f0; font-size: 13px; font-weight: 600; }
            QLabel#statusLabel { color: #eeeeee; font-weight: 600; }
            QLabel#hintLabel { color: #8f9295; }
            QLabel#versionLabel {
                color: #777a7d; font-size: 11px; font-weight: 400;
                padding: 0 0 2px 0;
            }
            QFrame#updateSection {
                background: transparent; border-top: 1px solid #424242;
            }
            QToolButton#repositoryButton {
                background: transparent; color: #cfd1d3;
                border: none; padding: 0; text-align: left;
            }
            QToolButton#repositoryButton:hover {
                background: transparent; color: #f0f0f0;
            }
            QToolButton#repositoryButton:pressed {
                background: transparent; color: #aeb1b4;
            }
            QLabel#updateTitleLabel {
                color: #d9d9d9; font-size: 12px; font-weight: 600;
            }
            QLabel#updateStatusLabel {
                color: #96999c; font-size: 11px;
            }
            QLabel#versionLabel[updateState="available"],
            QLabel#updateStatusLabel[updateState="available"] {
                color: #63c879; font-weight: 600;
            }
            QPushButton#updateButton {
                min-height: 30px; padding: 0 10px;
            }
            QPushButton#updateButton[updateState="available"] {
                background: #347d49; color: #f4fff6;
                border: 1px solid #51a867;
            }
            QPushButton#updateButton[updateState="available"]:hover {
                background: #3d9255; border-color: #67c87c;
            }
            QPushButton#updateButton[updateState="available"]:pressed {
                background: #2b6b3e; border-color: #438e57;
            }
            QProgressBar#updateProgress {
                background: #202020; border: none; border-radius: 1px;
            }
            QProgressBar#updateProgress::chunk {
                background: #2d8fe8; border-radius: 1px;
            }
            QFrame#settingsSidebar { background: #2b2b2b; }
            QFrame#settingsEditor { background: #303030; }
            QFrame#editorFooter { border-top: 1px solid #414141; }
            QStackedWidget#sidebarStack { background: #2b2b2b; }
            QStackedWidget#commandContentStack, QWidget#combinationPage,
            QStackedWidget#editorContentStack, QWidget#shortcutEditorPage,
            QScrollArea#shortcutAssignmentScroll,
            QWidget#shortcutAssignmentHost {
                background: #303030; border: none;
            }
            QLabel#shortcutEmptyLabel {
                color: #8f9295; font-size: 12px; padding: 20px;
            }
            QFrame#shortcutAssignmentRow {
                background: #343434; border: none;
                border-bottom: 1px solid #454545;
            }
            QFrame#shortcutAssignmentRow:hover { background: #393939; }
            QLabel#shortcutAssignmentIcon { background: transparent; border: none; }
            QLabel#shortcutAssignmentName {
                color: #eeeeee; font-size: 12px; background: transparent;
            }
            QToolButton#shortcutDeleteButton {
                min-width: 34px; max-width: 34px;
                min-height: 34px; max-height: 34px;
                background: transparent; border: 1px solid transparent;
                border-radius: 3px; padding: 4px;
            }
            QToolButton#shortcutDeleteButton:hover {
                background: #4a3434; border-color: #714848;
            }
            QFrame#modeTabBar { border-bottom: 1px solid #3d3d3d; }
            QToolButton#modeTab {
                min-width: 62px; min-height: 34px; background: transparent;
                color: #96999c; border: none; padding: 0 10px; font-size: 12px;
            }
            QToolButton#modeTab:hover { color: #d8dadd; background: #303030; }
            QToolButton#modeTab[active="true"] {
                color: #f0f1f2; font-weight: 600;
            }
            QFrame#modeTabIndicator { background: transparent; border: none; }
            QFrame#modeTabIndicator[active="true"] { background: #2d8fe8; }
            QScrollArea#menuPresetScroll, QWidget#menuCardsHost {
                background: #303030; border: none;
            }
            QPushButton#newMenuButton {
                min-height: 38px; background: transparent; color: #4ba7f4;
                border: 1px dashed #575a5d; border-radius: 4px; text-align: left;
                padding-left: 12px;
            }
            QPushButton#newMenuButton:hover {
                background: #333537; border-color: #73777a;
            }
            QFrame#menuPresetCard {
                background: #343434; border: 1px solid #454545;
                border-left: 3px solid transparent; border-radius: 4px;
            }
            QFrame#menuPresetCard:hover { background: #393939; border-color: #575757; }
            QFrame#menuPresetCard[selected="true"] {
                background: #383a3c; border: 1px solid #2d8fe8;
                border-left: 3px solid #4ba7f4;
            }
            QLabel#menuPresetName { color: #eeeeee; font-size: 13px; font-weight: 600; }
            QLabel#menuPresetCount { color: #929598; font-size: 11px; }
            QToolButton#presetActionButton {
                background: transparent; border: none; border-radius: 3px; padding: 2px;
            }
            QToolButton#presetActionButton:hover { background: #4a4c4e; }
            QSplitter::handle { background: #454545; width: 1px; }
            QLineEdit, QComboBox, QKeySequenceEdit {
                min-height: 30px; background: #2b2b2b; border: 1px solid #484848;
                border-radius: 3px; padding: 2px 8px; color: #eeeeee;
            }
            QLineEdit#shortcutValueEdit,
            QLineEdit#numericValueEdit {
                min-height: 34px; max-height: 34px;
                background: #303030; border: 1px solid #505050;
                border-radius: 3px; padding: 0 10px; color: #eeeeee;
            }
            QLineEdit#shortcutValueEdit:focus,
            QLineEdit#numericValueEdit:focus {
                background: #303030; border-color: #2d8fe8;
            }
            QLineEdit:focus, QComboBox:focus, QKeySequenceEdit:focus {
                border-color: #2d8fe8;
            }
            QListWidget {
                background: #303030; border: none; outline: none; padding: 2px 0;
            }
            QListWidget::item {
                padding: 2px 8px; margin: 0; border: 1px solid transparent;
                border-radius: 3px;
            }
            QListWidget::item:hover {
                background: #3a3a3a; color: #f0f0f0;
            }
            QListWidget::item:selected {
                background: #383a3c; color: #ffffff; border-color: #2d8fe8;
            }
            QListWidget::item:selected:hover {
                background: #3a3b3c; border-color: #3b9af0;
            }
            QPushButton {
                min-height: 32px; background: #333333; color: #e6e6e6;
                border: 1px solid #505050; border-radius: 3px;
            }
            QPushButton:hover { background: #3b3b3b; border-color: #686868; }
            QPushButton:pressed { background: #262626; }
            QPushButton#primaryButton {
                background: #2d8fe8; color: white; border-color: #3b9af0; font-weight: 600;
            }
            QPushButton#primaryButton:hover { background: #3599ef; }
            QToolButton#propertiesButton {
                min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
                background: #303030; color: #e5e5e5; border: 1px solid #4a4a4a;
                border-radius: 3px; padding: 0;
            }
            QToolButton#propertiesButton:hover {
                background: #3a3a3a; border-color: #686868;
            }
            QWidget#resettableField { background: transparent; }
            QToolButton#fieldResetButton {
                min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px;
                background: transparent; color: #d8d8d8; border: 1px solid transparent;
                border-radius: 3px; padding: 0;
            }
            QToolButton#fieldResetButton:hover {
                background: #3a3a3a; border-color: #565656;
            }
            QToolButton#fieldResetButton:pressed { background: #272727; }
            QToolButton#backButton, QToolButton#toolbarButton {
                min-height: 30px; background: #303030; color: #e5e5e5;
                border: 1px solid #4a4a4a; border-radius: 3px; padding: 1px 7px;
            }
            QToolButton#backButton:hover,
            QToolButton#toolbarButton:hover { background: #3a3a3a; border-color: #686868; }
            QToolButton#backButton { background: transparent; border: none; padding: 0; }
        """)

    def load_config(self, config):
        self._loading = True
        self.working_config = copy.deepcopy(config)
        removed_filter_ids = _merge_filter_catalog(
            self.working_config, force=True)
        _normalize_menu_presets(self.working_config)
        _normalize_command_shortcuts(self.working_config)
        if removed_filter_ids:
            try:
                _save_config(self.working_config)
                _reload_config_action()
                _log("Removed stale filter commands: " + ", ".join(
                    removed_filter_ids))
            except Exception:
                _error("Failed to persist stale filter cleanup:\n" + traceback.format_exc())
        self._populate_command_list()
        self._rebuild_menu_cards()
        self._show_commands_page()

        language = str(self.working_config.get("language", "painter"))
        if language == "auto":
            language = "painter"
        language_index = self.language_combo.findData(language)
        self.language_combo.setCurrentIndex(max(0, language_index))
        self.shortcut_edit.setKeySequence(_shortcut_sequence(self.working_config))
        self.auto_update_checkbox.setChecked(bool(
            self.working_config.get("auto_check_updates", True)))
        self.wheel_radius_spin.setValue(int(self.working_config.get("wheel_radius", 154)))
        self.inner_radius_spin.setMaximum(max(45, self.wheel_radius_spin.value() - 28))
        self.inner_radius_spin.setValue(int(self.working_config.get("inner_radius", 76)))
        self._highlight_color = QtGui.QColor(
            str(self.working_config.get("highlight_color", "#2d8fe8")))
        self._update_color_button()
        self._loading = False
        self._retranslate_ui()
        self.preview.set_config(self.working_config)

    def _populate_command_list(self, selected_id=""):
        self.command_list.clear()
        items = list(self.working_config.get("items", []))
        pending_thumbnails = []
        selected_item = None
        first_active_item = None
        for category in COMMAND_CATEGORY_ORDER:
            category_items = [
                item for item in items if _command_category(item) == category]
            if not category_items:
                continue
            active = [item for item in category_items if item.get("enabled", True)]
            inactive = sorted(
                (item for item in category_items if not item.get("enabled", True)),
                key=lambda item: _localized_value(
                    item, "labels", self.working_config).casefold())

            header = QtWidgets.QListWidgetItem(
                _tr(COMMAND_CATEGORY_KEYS[category], self.working_config))
            header.setData(ROLE_ROW_KIND, ROW_CATEGORY)
            header.setData(ROLE_CATEGORY, category)
            header.setFlags(QtCore.Qt.ItemIsEnabled)
            self.command_list.addItem(header)

            for item in active + inactive:
                list_item = QtWidgets.QListWidgetItem(
                    _localized_value(item, "labels", self.working_config))
                item_id = str(item.get("id", ""))
                list_item.setData(ROLE_ITEM_ID, item_id)
                list_item.setData(ROLE_ITEM_ENABLED, bool(item.get("enabled", True)))
                list_item.setData(ROLE_ROW_KIND, ROW_COMMAND)
                list_item.setData(ROLE_CATEGORY, category)
                list_item.setFlags(
                    (list_item.flags() | QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled)
                    & ~QtCore.Qt.ItemIsUserCheckable
                    & ~QtCore.Qt.ItemIsDragEnabled)
                pixmap = _item_icon_pixmap(item)
                if not pixmap.isNull():
                    list_item.setIcon(QtGui.QIcon(pixmap))
                resource_url = str(item.get("resource_url", "") or "")
                if resource_url and resource_url not in _RESOURCE_PIXMAP_CACHE:
                    pending_thumbnails.append((item_id, copy.deepcopy(item)))
                if not item.get("enabled", True):
                    list_item.setForeground(QtGui.QColor("#858585"))
                    list_item.setToolTip(_tr("restore_command", self.working_config))
                self.command_list.addItem(list_item)
                if first_active_item is None and item.get("enabled", True):
                    first_active_item = list_item
                if item_id == selected_id:
                    selected_item = list_item

        if selected_item is not None:
            self.command_list.setCurrentItem(selected_item)
        elif first_active_item is not None:
            self.command_list.setCurrentItem(first_active_item)
        self.shortcut_command_list.clear()
        current_item = self.command_list.currentItem()
        for row in range(self.command_list.count()):
            source_item = self.command_list.item(row)
            shortcut_item = source_item.clone()
            self.shortcut_command_list.addItem(shortcut_item)
            if source_item is current_item:
                self.shortcut_command_list.setCurrentItem(shortcut_item)
        self._filter_items(self.search_edit.text())
        self._thumbnail_queue = pending_thumbnails
        if self._thumbnail_queue and not self._thumbnail_timer.isActive():
            self._thumbnail_timer.start(1)

    def _load_next_thumbnail(self):
        if not self._thumbnail_queue or not self.isVisible():
            return
        item_id, item = self._thumbnail_queue.pop(0)
        pixmap = _item_icon_pixmap(item, load_resource=True)
        if not pixmap.isNull():
            for list_widget in (
                    self.command_list, self.shortcut_command_list):
                for row in range(list_widget.count()):
                    list_item = list_widget.item(row)
                    if str(list_item.data(ROLE_ITEM_ID) or "") == item_id:
                        list_item.setIcon(QtGui.QIcon(pixmap))
                        break
            shortcut_row = self.shortcut_assignment_rows.get(item_id)
            if shortcut_row is not None:
                shortcut_row["icon"].setPixmap(pixmap.scaled(
                    24, 24, QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation))
            self.preview.update()
            if _POPUP is not None:
                _POPUP.update()
        if self._thumbnail_queue:
            self._thumbnail_timer.start(8)

    def _update_shortcut_assignment_visibility(self):
        has_rows = bool(self.shortcut_assignment_rows)
        self.shortcut_empty_label.setVisible(not has_rows)
        self.shortcut_assignment_scroll.setVisible(has_rows)

    def _shortcut_delete_icon(self):
        return QtGui.QIcon(_icon_path("delete"))

    def _add_shortcut_assignment(self, command_id, shortcut=None, capture=False):
        command_id = str(command_id or "")
        existing = self.shortcut_assignment_rows.get(command_id)
        if existing is not None:
            edit = existing["edit"]
            self.shortcut_assignment_scroll.ensureWidgetVisible(existing["widget"])
            if capture:
                edit._begin_capture()
            else:
                edit.setFocus(QtCore.Qt.OtherFocusReason)
            return

        item = self._items_by_id().get(command_id)
        if item is None:
            return

        row = QtWidgets.QFrame(self.shortcut_assignment_host)
        row.setObjectName("shortcutAssignmentRow")
        row.setFixedHeight(58)
        row.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        row_layout = QtWidgets.QHBoxLayout(row)
        row_layout.setContentsMargins(14, 9, 12, 9)
        row_layout.setSpacing(12)

        icon_label = QtWidgets.QLabel(row)
        icon_label.setObjectName("shortcutAssignmentIcon")
        icon_label.setFixedSize(28, 28)
        icon_label.setAlignment(QtCore.Qt.AlignCenter)
        pixmap = _item_icon_pixmap(item)
        if not pixmap.isNull():
            icon_label.setPixmap(pixmap.scaled(
                24, 24, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        row_layout.addWidget(icon_label, 0, QtCore.Qt.AlignVCenter)

        name_label = QtWidgets.QLabel(
            _localized_value(item, "labels", self.working_config), row)
        name_label.setObjectName("shortcutAssignmentName")
        name_label.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        name_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        row_layout.addWidget(name_label, 1, QtCore.Qt.AlignVCenter)

        edit = ShortcutCaptureEdit(row)
        edit.setFixedSize(220, 36)
        edit.set_capture_prompt(_tr("press_key", self.working_config))
        edit.setKeySequence(_shortcut_sequence_from_value(shortcut or {}))
        edit.keySequenceChanged.connect(
            lambda sequence, item_id=command_id:
            self._command_shortcut_changed(item_id, sequence))
        row_layout.addWidget(edit, 0, QtCore.Qt.AlignVCenter)

        delete_button = QtWidgets.QToolButton(row)
        delete_button.setObjectName("shortcutDeleteButton")
        delete_button.setIcon(self._shortcut_delete_icon())
        delete_button.setIconSize(QtCore.QSize(20, 20))
        delete_button.setFixedSize(36, 36)
        delete_button.setToolTip(_tr("remove_shortcut", self.working_config))
        delete_button.setFocusPolicy(QtCore.Qt.NoFocus)
        delete_button.clicked.connect(
            lambda checked=False, item_id=command_id:
            self._remove_shortcut_assignment(item_id))
        row_layout.addWidget(delete_button, 0, QtCore.Qt.AlignVCenter)

        self.shortcut_assignment_rows[command_id] = {
            "widget": row,
            "icon": icon_label,
            "name": name_label,
            "edit": edit,
            "delete": delete_button
        }
        self.shortcut_assignment_layout.addWidget(row)
        self._update_shortcut_assignment_visibility()
        if capture:
            self.shortcut_assignment_scroll.ensureWidgetVisible(row)
            edit._begin_capture()

    def _add_shortcut_from_list_item(self, list_item):
        if list_item.data(ROLE_ROW_KIND) != ROW_COMMAND:
            return
        command_id = str(list_item.data(ROLE_ITEM_ID) or "")
        self._add_shortcut_assignment(command_id, capture=True)
        self._sync_command_shortcuts()

    def _remove_shortcut_assignment(self, command_id):
        entry = self.shortcut_assignment_rows.pop(str(command_id or ""), None)
        if entry is None:
            return
        widget = entry["widget"]
        self.shortcut_assignment_layout.removeWidget(widget)
        widget.deleteLater()
        self._sync_command_shortcuts()
        self._update_shortcut_assignment_visibility()

    def _command_shortcut_changed(self, command_id, sequence):
        del command_id, sequence
        self._sync_command_shortcuts()

    def _sync_command_shortcuts(self):
        if self._loading:
            return
        assignments = []
        for command_id, entry in self.shortcut_assignment_rows.items():
            assignments.append({
                "command_id": command_id,
                "shortcut": _shortcut_value_from_sequence(
                    entry["edit"].keySequence())
            })
        self.working_config["command_shortcuts"] = assignments

    def _rebuild_shortcut_assignments(self):
        while self.shortcut_assignment_layout.count():
            layout_item = self.shortcut_assignment_layout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.deleteLater()
        self.shortcut_assignment_rows.clear()
        _normalize_command_shortcuts(self.working_config)
        for assignment in self.working_config.get("command_shortcuts", []):
            self._add_shortcut_assignment(
                assignment.get("command_id", ""),
                assignment.get("shortcut", {}))
        self._update_shortcut_assignment_visibility()

    def _preview_items_changed(self, items):
        selected_id = self.preview.selected_id
        self.working_config["items"] = copy.deepcopy(list(items))
        self._capture_active_menu_preset()
        self._populate_command_list(selected_id)
        self._rebuild_menu_cards()
        if hasattr(self, "status_label"):
            self._update_status()

    def _restore_command(self, list_item):
        if list_item.data(ROLE_ROW_KIND) != ROW_COMMAND:
            return
        item_id = str(list_item.data(ROLE_ITEM_ID) or "")
        item = self._items_by_id().get(item_id)
        if item is None or item.get("enabled", True):
            return
        item["enabled"] = True
        items = list(self.working_config.get("items", []))
        active = [entry for entry in items if entry.get("enabled", True) and entry is not item]
        inactive = [entry for entry in items if not entry.get("enabled", True)]
        self.working_config["items"] = active + [item] + inactive
        self._capture_active_menu_preset()
        self._populate_command_list(item_id)
        self.preview.set_config(self.working_config)
        self.preview.set_selected_id(item_id)
        self._rebuild_menu_cards()

    def _filter_items(self, text):
        query = str(text).strip().casefold()
        items_by_id = self._items_by_id()
        for list_widget in (self.command_list, self.shortcut_command_list):
            visible_categories = set()
            for row in range(list_widget.count()):
                item = list_widget.item(row)
                if item.data(ROLE_ROW_KIND) != ROW_COMMAND:
                    continue
                category = str(item.data(ROLE_CATEGORY) or "other")
                command = items_by_id.get(
                    str(item.data(ROLE_ITEM_ID) or ""), {})
                searchable = [item.text(), str(command.get("id", ""))]
                for field in ("labels", "descriptions"):
                    values = command.get(field, {})
                    if isinstance(values, dict):
                        searchable.extend(
                            str(value) for value in values.values())
                    elif values:
                        searchable.append(str(values))
                category_key = COMMAND_CATEGORY_KEYS.get(
                    category, "category_other")
                searchable.extend(
                    TRANSLATIONS.get(language, {}).get(category_key, "")
                    for language in ("en", "zh_CN"))
                haystack = "\n".join(searchable).casefold()
                visible = not query or query in haystack
                item.setHidden(not visible)
                if visible:
                    visible_categories.add(category)
            for row in range(list_widget.count()):
                item = list_widget.item(row)
                if item.data(ROLE_ROW_KIND) == ROW_CATEGORY:
                    item.setHidden(
                        str(item.data(ROLE_CATEGORY)) not in visible_categories)

    def _show_properties_page(self):
        self.sidebar_stack.setCurrentWidget(self.properties_page)

    def _active_menu_preset(self):
        active_id = str(self.working_config.get("active_menu_id", "") or "")
        for menu in self.working_config.get("menus", []):
            if str(menu.get("id", "")) == active_id:
                return menu
        return None

    def _capture_active_menu_preset(self):
        menu = self._active_menu_preset()
        if menu is None:
            return
        menu["item_ids"] = [
            str(item.get("id", ""))
            for item in self.working_config.get("items", [])
            if item.get("id") and item.get("enabled", True)]

    def _rebuild_menu_cards(self):
        if not hasattr(self, "menu_cards_layout"):
            return
        while self.menu_cards_layout.count():
            layout_item = self.menu_cards_layout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.deleteLater()
        menus = list(self.working_config.get("menus", []))
        active_id = str(self.working_config.get("active_menu_id", "") or "")
        for menu in menus:
            menu_id = str(menu.get("id", ""))
            card = MenuPresetCard(menu_id, self.menu_cards_host)
            card.set_data(
                _menu_preset_name(menu, self.working_config),
                _tr("menu_item_count", self.working_config).format(
                    count=len(menu.get("item_ids", []))),
                menu_id == active_id,
                menu_id != "default" and len(menus) > 1,
                self.working_config)
            card.activated.connect(self._activate_menu_preset)
            card.renameRequested.connect(self._rename_menu_preset)
            card.deleteRequested.connect(self._delete_menu_preset)
            self.menu_cards_layout.addWidget(card)
        self.menu_cards_layout.addStretch(1)

    def _activate_menu_preset(self, menu_id):
        menu_id = str(menu_id or "")
        if not menu_id or menu_id == self.working_config.get("active_menu_id"):
            return
        self._capture_active_menu_preset()
        target = next((
            menu for menu in self.working_config.get("menus", [])
            if str(menu.get("id", "")) == menu_id), None)
        if target is None:
            return

        items = list(self.working_config.get("items", []))
        by_id = {str(item.get("id", "")): item for item in items}
        requested_ids = [
            str(item_id) for item_id in target.get("item_ids", [])
            if str(item_id) in by_id]
        requested = set(requested_ids)
        for item in items:
            item["enabled"] = str(item.get("id", "")) in requested
        ordered = [by_id[item_id] for item_id in requested_ids]
        ordered.extend(
            item for item in items
            if str(item.get("id", "")) not in requested)
        self.working_config["items"] = ordered
        self.working_config["active_menu_id"] = menu_id
        self._populate_command_list(requested_ids[0] if requested_ids else "")
        self.preview.set_config(self.working_config)
        self._rebuild_menu_cards()
        self._update_status()

    def _create_menu_preset(self):
        self._capture_active_menu_preset()
        menus = self.working_config.setdefault("menus", [])
        menu_number = len(menus) + 1
        menu_id = "menu_" + hashlib.sha1(
            (str(time.time_ns()) + str(menu_number)).encode("utf-8")).hexdigest()[:12]
        menus.append({
            "id": menu_id,
            "name": _tr("new_menu_name", self.working_config).format(
                number=menu_number),
            "item_ids": [
                str(item.get("id", ""))
                for item in self.working_config.get("items", [])
                if item.get("id") and item.get("enabled", True)]
        })
        self.working_config["active_menu_id"] = menu_id
        self._rebuild_menu_cards()

    def _rename_menu_preset(self, menu_id):
        menu = next((
            entry for entry in self.working_config.get("menus", [])
            if str(entry.get("id", "")) == str(menu_id)), None)
        if menu is None:
            return
        current_name = _menu_preset_name(menu, self.working_config)
        name, accepted = QtWidgets.QInputDialog.getText(
            self,
            _tr("rename_menu", self.working_config),
            _tr("menu_name_prompt", self.working_config),
            QtWidgets.QLineEdit.Normal,
            current_name)
        name = str(name).strip()
        if accepted and name:
            menu["name"] = name
            self._rebuild_menu_cards()

    def _delete_menu_preset(self, menu_id):
        menu_id = str(menu_id or "")
        menus = self.working_config.get("menus", [])
        menu = next((
            entry for entry in menus
            if str(entry.get("id", "")) == menu_id), None)
        if menu is None or menu_id == "default" or len(menus) <= 1:
            return
        name = _menu_preset_name(menu, self.working_config)
        response = QtWidgets.QMessageBox.question(
            self,
            _tr("delete_menu", self.working_config),
            _tr("delete_menu_confirm", self.working_config).format(name=name),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if response != QtWidgets.QMessageBox.Yes:
            return
        was_active = self.working_config.get("active_menu_id") == menu_id
        self.working_config["menus"] = [
            entry for entry in menus
            if str(entry.get("id", "")) != menu_id]
        if was_active:
            fallback_id = str(self.working_config["menus"][0].get("id", ""))
            self.working_config["active_menu_id"] = ""
            self._activate_menu_preset(fallback_id)
        else:
            self._rebuild_menu_cards()
        self._apply()

    def _set_command_mode(self, index):
        index = int(index)
        if index not in (0, 1, 2):
            index = 0
        self.command_content_stack.setCurrentIndex(index)
        self.command_tab_button.setChecked(index == 0)
        self.combination_tab_button.setChecked(index == 1)
        self.shortcut_tab_button.setChecked(index == 2)
        for widget, active in (
                (self.command_tab_button, index == 0),
                (self.command_tab_indicator, index == 0),
                (self.combination_tab_button, index == 1),
                (self.combination_tab_indicator, index == 1),
                (self.shortcut_tab_button, index == 2),
                (self.shortcut_tab_indicator, index == 2)):
            widget.setProperty("active", bool(active))
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
        self.search_edit.setEnabled(index in (0, 2))
        if hasattr(self, "editor_content_stack"):
            self.editor_content_stack.setCurrentIndex(1 if index == 2 else 0)
        if hasattr(self, "hint_label"):
            self.hint_label.setVisible(index != 2)
        if hasattr(self, "status_label"):
            self._update_status()
        if index in (0, 2):
            self.search_edit.setFocus(QtCore.Qt.OtherFocusReason)

    def _show_commands_page(self):
        self.sidebar_stack.setCurrentWidget(self.command_page)
        if self.command_content_stack.currentIndex() in (0, 2):
            self.search_edit.setFocus(QtCore.Qt.OtherFocusReason)

    def _update_status(self):
        list_widget = self.command_list
        if (hasattr(self, "command_content_stack")
                and self.command_content_stack.currentIndex() == 2):
            list_widget = self.shortcut_command_list
        current = list_widget.currentItem()
        item_id = str(current.data(ROLE_ITEM_ID) or "") if current else ""
        item = self._items_by_id().get(item_id)
        name = (
            _localized_value(item, "labels", self.working_config)
            if item is not None else "")
        self.status_label.setText(
            _tr("selected_status", self.working_config).format(name=name))

    def _items_by_id(self):
        return {
            str(item.get("id", "")): item
            for item in self.working_config.get("items", [])
        }

    def _sync_item_order(self):
        self.working_config["items"] = copy.deepcopy(
            self.preview.config.get("items", self.working_config.get("items", [])))
        self._capture_active_menu_preset()

    def _selection_changed(self):
        current = self.command_list.currentItem()
        item_id = (
            current.data(ROLE_ITEM_ID)
            if current is not None and current.data(ROLE_ROW_KIND) == ROW_COMMAND
            else "")
        self.preview.set_selected_id(item_id)
        self._update_status()

    def _select_item_by_id(self, item_id):
        for row in range(self.command_list.count()):
            item = self.command_list.item(row)
            if str(item.data(ROLE_ITEM_ID) or "") == item_id:
                self.command_list.setCurrentRow(row)
                return

    def _shortcut_config(self):
        return _shortcut_value_from_sequence(
            self.shortcut_edit.keySequence(), DEFAULT_CONFIG["shortcut"])

    def _sync_controls(self):
        if self._loading:
            return
        self.working_config["language"] = str(self.language_combo.currentData() or "painter")
        self.working_config["shortcut"] = self._shortcut_config()
        self.working_config["auto_check_updates"] = bool(
            self.auto_update_checkbox.isChecked())
        self.working_config["wheel_radius"] = self.wheel_radius_spin.value()
        self.working_config["inner_radius"] = min(
            self.inner_radius_spin.value(), self.wheel_radius_spin.value() - 28)
        self.working_config["highlight_color"] = self._highlight_color.name()
        self._sync_command_shortcuts()
        self._capture_active_menu_preset()

    def _controls_changed(self, *args):
        del args
        self._sync_controls()
        self.preview.set_config(self.working_config)

    def _language_changed(self, *args):
        del args
        if self._loading:
            return
        self._sync_controls()
        self._retranslate_ui()
        self.preview.set_config(self.working_config)

    def _retranslate_ui(self):
        config = self.working_config
        self.setWindowTitle(_tr("settings_title", config))
        self.search_edit.setPlaceholderText(_tr("search_commands", config))
        self.command_tab_button.setText(_tr("tab_commands", config))
        self.combination_tab_button.setText(_tr("tab_combinations", config))
        self.shortcut_tab_button.setText(_tr("tab_shortcuts", config))
        self.shortcut_empty_label.setText(_tr("shortcut_empty", config))
        self.new_menu_button.setText("+ " + _tr("new_menu", config))
        self.appearance_title.setText(_tr("wheel_properties", config))
        self.properties_button.setToolTip(_tr("open_properties", config))
        self.back_button.setToolTip(_tr("back_to_commands", config))
        self.shortcut_edit.set_capture_prompt(_tr("press_key", config))
        self.shortcut_reset_button.setToolTip(_tr("reset_shortcut", config))
        self.wheel_radius_reset_button.setToolTip(
            _tr("reset_wheel_radius", config))
        self.inner_radius_reset_button.setToolTip(
            _tr("reset_inner_radius", config))
        for key, label in self.form_labels.items():
            label.setText(_tr(key, config))
        self.reset_button.setText(_tr("reset", config))
        self.apply_button.setText(_tr("apply", config))
        self.close_button.setText(_tr("close", config))
        self.color_button.setToolTip(_tr("highlight_color", config))
        self.hint_label.setText(_tr("editor_hint", config))
        self.update_title_label.setText(_tr("updates", config))
        self.auto_update_checkbox.setText(_tr("auto_check_updates", config))
        repository_tooltip = _tr("open_repository", config) + "\n" + GITHUB_REPOSITORY_URL
        self.repository_button.setToolTip(repository_tooltip)
        self.repository_button.setAccessibleName(repository_tooltip)

        was_blocked = self.language_combo.blockSignals(True)
        self.language_combo.setItemText(0, _tr("language_painter", config))
        self.language_combo.setItemText(1, _tr("language_zh", config))
        self.language_combo.setItemText(2, _tr("language_en", config))
        self.language_combo.blockSignals(was_blocked)

        current = self.command_list.currentItem()
        selected_id = str(current.data(ROLE_ITEM_ID) or "") if current else ""
        self._populate_command_list(selected_id)

        self._rebuild_menu_cards()
        self._rebuild_shortcut_assignments()
        self._update_status()
        self._refresh_update_ui()

    def _bind_update_manager(self):
        manager = _ensure_update_manager()
        if manager is self._update_manager:
            self._refresh_update_ui()
            return
        if self._update_manager is not None:
            try:
                self._update_manager.stateChanged.disconnect(
                    self._refresh_update_ui)
                self._update_manager.progressChanged.disconnect(
                    self._update_progress_changed)
            except (RuntimeError, TypeError):
                pass
        self._update_manager = manager
        if manager is not None:
            manager.stateChanged.connect(self._refresh_update_ui)
            manager.progressChanged.connect(self._update_progress_changed)
        self._refresh_update_ui()

    def _update_detail_text(self, detail):
        detail = str(detail or "").strip()
        if not detail:
            return _tr("network_error", self.working_config)
        translated = _tr(detail, self.working_config)
        return translated if translated != detail else detail

    def _refresh_update_ui(self):
        if not hasattr(self, "update_button"):
            return
        manager = self._update_manager
        state = manager.state if manager is not None else "idle"
        version = (
            manager.latest_version if manager is not None
            else PLUGIN_VERSION)
        progress = manager.progress if manager is not None else -1
        config = self.working_config

        status_text = ""
        button_text = _tr("check_updates", config)
        button_enabled = state in (
            "idle", "latest", "no_releases", "available", "restart", "error")
        show_progress = state in ("checking", "downloading", "installing")

        if state == "checking":
            status_text = _tr("checking_updates", config)
            button_text = status_text
        elif state == "no_releases":
            status_text = _tr("no_releases", config)
        elif state == "latest":
            status_text = _tr("up_to_date", config).format(
                version=PLUGIN_VERSION)
        elif state == "available":
            status_text = _tr("update_available", config).format(
                version=version)
            button_text = _tr("download_update", config)
        elif state == "downloading":
            status_text = _tr("downloading_update", config).format(
                progress=max(0, progress))
            button_text = _tr("downloading_update", config).format(
                progress=max(0, progress))
        elif state == "installing":
            status_text = _tr("installing_update", config)
            button_text = status_text
        elif state == "restart":
            status_text = _tr("restart_to_finish", config)
            button_text = _tr("restart_painter", config)
        elif state == "error":
            status_text = _tr("update_failed", config).format(
                detail=self._update_detail_text(manager.detail))

        self.update_status_label.setText(status_text)
        self.update_status_label.setVisible(bool(status_text))
        self.update_button.setText(button_text)
        self.update_button.setEnabled(button_enabled)
        for widget in (
                self.version_label,
                self.update_status_label,
                self.update_button):
            if widget.property("updateState") == state:
                continue
            widget.setProperty("updateState", state)
            style = widget.style()
            if style is not None:
                style.unpolish(widget)
                style.polish(widget)
            widget.update()
        self.update_progress.setVisible(show_progress)
        if show_progress:
            if state == "downloading" and progress >= 0:
                self.update_progress.setRange(0, 100)
                self.update_progress.setValue(max(0, min(100, progress)))
            else:
                self.update_progress.setRange(0, 0)

    def _update_progress_changed(self, value):
        del value
        self._refresh_update_ui()

    def _update_button_clicked(self):
        manager = self._update_manager
        if manager is None:
            self._bind_update_manager()
            manager = self._update_manager
        if manager is None:
            return
        if manager.state == "restart":
            manager.prompt_restart(self)
            return
        if manager.state != "available":
            manager.check_for_updates(force=True)
            return

        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle(
            _tr("update_confirm_title", self.working_config))
        dialog.setIcon(QtWidgets.QMessageBox.Information)
        dialog.setTextFormat(QtCore.Qt.PlainText)
        dialog.setText(_tr(
            "update_confirm_message", self.working_config).format(
                version=manager.latest_version))
        install_button = dialog.addButton(
            _tr("confirm_install", self.working_config),
            QtWidgets.QMessageBox.AcceptRole)
        cancel_button = dialog.addButton(
            _tr("close", self.working_config),
            QtWidgets.QMessageBox.RejectRole)
        dialog.setDefaultButton(install_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec()
        if dialog.clickedButton() is install_button:
            manager.download_and_install()

    def _open_repository(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(GITHUB_REPOSITORY_URL))

    def _wheel_radius_changed(self, value):
        self.inner_radius_spin.setMaximum(max(45, int(value) - 28))
        self._controls_changed()

    def _choose_highlight_color(self):
        color = QtWidgets.QColorDialog.getColor(
            self._highlight_color, self, _tr("highlight_color", self.working_config))
        if color.isValid():
            self._highlight_color = color
            self._update_color_button()
            self._controls_changed()

    def _update_color_button(self):
        self.color_button.setStyleSheet(
            "QPushButton { background: %s; border: 1px solid #55585b; border-radius: 3px; }"
            % self._highlight_color.name())

    def _reset_shortcut(self):
        self.shortcut_edit.setKeySequence(_shortcut_sequence(DEFAULT_CONFIG))

    def _reset_wheel_radius(self):
        self.wheel_radius_spin.setValue(int(DEFAULT_CONFIG["wheel_radius"]))

    def _reset_inner_radius(self):
        self.inner_radius_spin.setValue(int(DEFAULT_CONFIG["inner_radius"]))

    def _reset_defaults(self):
        dialog = QtWidgets.QMessageBox(self)
        dialog.setWindowTitle(
            _tr("reset_confirm_title", self.working_config))
        dialog.setIcon(QtWidgets.QMessageBox.Warning)
        dialog.setTextFormat(QtCore.Qt.PlainText)
        dialog.setText(
            _tr("reset_confirm_message", self.working_config))
        restore_button = dialog.addButton(
            _tr("reset", self.working_config),
            QtWidgets.QMessageBox.DestructiveRole)
        cancel_button = dialog.addButton(
            _tr("close", self.working_config),
            QtWidgets.QMessageBox.RejectRole)
        dialog.setDefaultButton(cancel_button)
        dialog.setEscapeButton(cancel_button)
        dialog.exec()
        if dialog.clickedButton() is not restore_button:
            return
        self.load_config(copy.deepcopy(DEFAULT_CONFIG))

    def _apply(self):
        self._sync_item_order()
        self._sync_controls()
        self._capture_active_menu_preset()
        try:
            _save_config(self.working_config)
            _reload_config_action()
            self.apply_button.show_success(
                _tr("applied", self.working_config),
                _tr("apply", self.working_config))
        except Exception:
            _error("Settings save failed:\n" + traceback.format_exc())
            QtWidgets.QMessageBox.warning(
                self,
                _tr("dialog_title", self.working_config),
                _tr("save_failed", self.working_config))

    def showEvent(self, event):
        if _KEY_FILTER is not None:
            _KEY_FILTER.release_hold(run_hovered=False)
        _hide_popup(run_hovered=False)
        screen = self.screen() or QtGui.QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            maximum_width = max(720, int(available.width() * 0.94))
            maximum_height = max(420, int(available.height() * 0.92))
            self.setMinimumSize(
                min(720, maximum_width), min(420, maximum_height))
            self.resize(
                min(self.width(), maximum_width),
                min(self.height(), maximum_height))
        self._show_commands_page()
        super().showEvent(event)
        self._bind_update_manager()
        if self._thumbnail_queue and not self._thumbnail_timer.isActive():
            self._thumbnail_timer.start(1)

    def shutdown(self):
        if self._update_manager is not None:
            try:
                self._update_manager.stateChanged.disconnect(
                    self._refresh_update_ui)
                self._update_manager.progressChanged.disconnect(
                    self._update_progress_changed)
            except (RuntimeError, TypeError):
                pass
            self._update_manager = None
        if self.shortcut_edit._capturing:
            self.shortcut_edit._cancel_capture()
        self._thumbnail_timer.stop()
        self._thumbnail_queue = []


class ToolsPopup(QtWidgets.QFrame):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = {}
        self.current_action = ""
        self.hovered_index = -1
        self._disabled_actions = set()
        self._is_shutting_down = False
        self._prepared = False
        popup_flags = QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint
        no_shadow = getattr(QtCore.Qt, "NoDropShadowWindowHint", None)
        if no_shadow is not None:
            popup_flags |= no_shadow
        self.setWindowFlags(popup_flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)
        self.set_config(config)

    def set_config(self, config):
        self.config = copy.deepcopy(config)
        self.current_action = ""
        self.hovered_index = -1
        outer_radius = int(self.config.get("wheel_radius", 154))
        margin = max(18, int(self.config.get("wheel_margin", 22)))
        size = (outer_radius + margin) * 2
        self.setFixedSize(size, size)
        self.update()

    def prepare(self):
        if self._prepared:
            return
        self.ensurePolished()
        surface = QtGui.QImage(
            self.size(), QtGui.QImage.Format_ARGB32_Premultiplied)
        surface.fill(QtCore.Qt.transparent)
        self.render(surface)
        self.winId()
        self._prepared = True

    def _items(self):
        return [
            item for item in self.config.get("items", [])
            if item.get("enabled", True)
        ]

    def refresh_action_states(self):
        self._disabled_actions = {
            item.get("action", "") for item in self._items()
            if _action_unavailable(item.get("action", ""))
        }
        items = self._items()
        if 0 <= self.hovered_index < len(items):
            action = items[self.hovered_index].get("action", "")
            self.current_action = "" if action in self._disabled_actions else action
        else:
            self.current_action = ""
        self.update()

    @staticmethod
    def _point(center, radius, angle_degrees):
        angle = math.radians(angle_degrees)
        return QtCore.QPointF(
            center.x() + math.cos(angle) * radius,
            center.y() + math.sin(angle) * radius)

    def _segment_path(self, center, outer_radius, inner_radius, start, end):
        sweep = end - start
        outer_rect = QtCore.QRectF(
            center.x() - outer_radius,
            center.y() - outer_radius,
            outer_radius * 2,
            outer_radius * 2)
        inner_rect = QtCore.QRectF(
            center.x() - inner_radius,
            center.y() - inner_radius,
            inner_radius * 2,
            inner_radius * 2)
        path = QtGui.QPainterPath()
        path.arcMoveTo(outer_rect, -start)
        path.arcTo(outer_rect, -start, -sweep)
        path.lineTo(self._point(center, inner_radius, end))
        path.arcTo(inner_rect, -end, sweep)
        path.closeSubpath()
        return path

    def _arc_path(self, center, radius, start, end):
        arc_rect = QtCore.QRectF(
            center.x() - radius,
            center.y() - radius,
            radius * 2,
            radius * 2)
        path = QtGui.QPainterPath()
        path.arcMoveTo(arc_rect, -start)
        path.arcTo(arc_rect, -start, -(end - start))
        return path

    def _geometry(self):
        center = QtCore.QPointF(self.width() * 0.5, self.height() * 0.5)
        outer_radius = float(self.config.get("wheel_radius", 154))
        inner_radius = float(self.config.get("inner_radius", 76))
        return center, outer_radius, min(inner_radius, outer_radius - 28)

    def _segment_index_at(self, position):
        items = self._items()
        if not items:
            return -1
        center, outer_radius, inner_radius = self._geometry()
        dx = position.x() - center.x()
        dy = position.y() - center.y()
        distance = math.sqrt(dx * dx + dy * dy)
        if distance < inner_radius:
            return -1
        span = 360.0 / len(items)
        angle = math.degrees(math.atan2(dy, dx))
        index = int(((angle - (-90.0 - span * 0.5)) + 360.0) % 360.0 / span)
        return min(len(items) - 1, index)

    def update_from_global_position(self, position=None):
        global_position = position
        if global_position is None:
            global_position = QtGui.QCursor.pos()
        if isinstance(global_position, QtCore.QPointF):
            global_position = global_position.toPoint()
        local_position = self.mapFromGlobal(global_position)
        self._update_hover(QtCore.QPointF(local_position))

    def _update_hover(self, position):
        index = self._segment_index_at(position)
        if index == self.hovered_index:
            return
        self.hovered_index = index
        items = self._items()
        action = items[index].get("action", "") if index >= 0 else ""
        self.current_action = "" if action in self._disabled_actions else action
        self.update()

    def paintEvent(self, event):
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        center, outer_radius, inner_radius = self._geometry()
        items = self._items()
        count = max(1, len(items))
        span = 360.0 / count
        highlight = QtGui.QColor(str(self.config.get("highlight_color", "#2d8fe8")))

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 55))
        painter.drawEllipse(center, outer_radius + 7, outer_radius + 7)

        hovered_highlight = None
        for index in range(count):
            item = items[index] if index < len(items) else None
            action = item.get("action", "") if item is not None else ""
            disabled = action in self._disabled_actions
            center_angle = -90.0 + span * index
            start = center_angle - span * 0.5
            end = center_angle + span * 0.5
            hovered = index == self.hovered_index
            path = self._segment_path(center, outer_radius, inner_radius, start, end)
            if hovered and disabled:
                painter.setBrush(QtGui.QColor("#4a292c"))
                painter.setPen(QtGui.QPen(QtGui.QColor("#a94c52"), 1))
            else:
                painter.setBrush(QtGui.QColor("#343638" if hovered else "#272727"))
                painter.setPen(QtGui.QPen(
                    QtGui.QColor("#5b5e61" if hovered else "#484848"), 1))
            painter.drawPath(path)
            if hovered:
                hovered_highlight = (start, end, disabled)

            if item is None:
                continue
            icon = _item_icon_pixmap(item)
            if not icon.isNull():
                icon_radius = (outer_radius + inner_radius) * 0.5
                icon_center = self._point(center, icon_radius, center_angle)
                icon_size = int(self.config.get("icon_size", 25))
                target = QtCore.QRectF(
                    icon_center.x() - icon_size * 0.5,
                    icon_center.y() - icon_size * 0.5,
                    icon_size, icon_size)
                painter.setOpacity(1.0 if hovered else (0.48 if disabled else 0.72))
                painter.drawPixmap(target, icon, QtCore.QRectF(icon.rect()))
                painter.setOpacity(1.0)

        painter.setPen(QtGui.QPen(QtGui.QColor("#151515"), 7))
        painter.setBrush(QtCore.Qt.NoBrush)
        ring_radius = outer_radius - 3
        painter.drawEllipse(center, ring_radius, ring_radius)
        if hovered_highlight is not None:
            start, end, disabled = hovered_highlight
            active_color = QtGui.QColor("#e05252") if disabled else highlight
            glow_color = QtGui.QColor(active_color)
            glow_color.setAlpha(28)
            glow_pen = QtGui.QPen(glow_color, 10)
            glow_pen.setCapStyle(QtCore.Qt.FlatCap)
            painter.setPen(glow_pen)
            highlight_path = self._arc_path(center, ring_radius, start, end)
            painter.drawPath(highlight_path)
            highlight_pen = QtGui.QPen(active_color, 4)
            highlight_pen.setCapStyle(QtCore.Qt.FlatCap)
            painter.setPen(highlight_pen)
            painter.drawPath(highlight_path)
        painter.setPen(QtGui.QPen(QtGui.QColor("#505050"), 1.5))
        painter.setBrush(QtGui.QColor("#181818"))
        painter.drawEllipse(center, inner_radius, inner_radius)

        hovered_item = (
            items[self.hovered_index]
            if 0 <= self.hovered_index < len(items)
            else None)
        center_text = (
            _localized_value(hovered_item, "labels", self.config)
            if hovered_item is not None
            else _active_menu_name(self.config))
        hovered_action = (
            str(hovered_item.get("action", ""))
            if hovered_item is not None else "")
        if hovered_action in self._disabled_actions:
            center_color = QtGui.QColor("#ef8b8b")
        elif hovered_item is not None:
            center_color = QtGui.QColor("#f2f2f2")
        else:
            center_color = QtGui.QColor("#b8b8b8")
        painter.setPen(center_color)
        center_font = painter.font()
        center_font.setPixelSize(13)
        center_font.setBold(hovered_item is not None)
        painter.setFont(center_font)
        painter.drawText(
            QtCore.QRectF(
                center.x() - inner_radius * 0.78,
                center.y() - 22,
                inner_radius * 1.56,
                44),
            QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
            center_text)

    def mouseMoveEvent(self, event):
        self._update_hover(event.position())
        event.accept()

    def leaveEvent(self, event):
        if self.isVisible():
            self.update_from_global_position()
        super().leaveEvent(event)

    def release_and_close(self):
        if self._is_shutting_down:
            return
        action = self.current_action
        self.hide()
        if action:
            _run_action(action)

    def shutdown(self):
        self._is_shutting_down = True
        self.current_action = ""
        self.hovered_index = -1
        self.hide()
        self.setParent(None)
        self.deleteLater()

    def showEvent(self, event):
        super().showEvent(event)
        self._is_shutting_down = False
        self.setFocus(QtCore.Qt.PopupFocusReason)


def _show_popup():
    global _POPUP
    config = _RUNTIME_CONFIG or _load_config()
    pos = QtGui.QCursor.pos()
    if _POPUP is not None and _POPUP.isVisible():
        _POPUP.refresh_action_states()
        _POPUP.move(pos.x() - _POPUP.width() // 2, pos.y() - _POPUP.height() // 2)
        _POPUP.raise_()
        return

    if _POPUP is None:
        _POPUP = ToolsPopup(config, substance_painter.ui.get_main_window())
        _POPUP.prepare()
    else:
        _POPUP.current_action = ""
        _POPUP.hovered_index = -1
    _POPUP.refresh_action_states()
    _POPUP.move(pos.x() - _POPUP.width() // 2, pos.y() - _POPUP.height() // 2)
    _POPUP.show()


def _hide_popup(run_hovered=True):
    global _POPUP
    if _POPUP is None or not _POPUP.isVisible():
        return
    if run_hovered:
        _POPUP.release_and_close()
    else:
        _POPUP.hide()


def _preload_runtime_resource_icons():
    config = _RUNTIME_CONFIG or {}
    pending = [
        item for item in config.get("items", [])
        if item.get("enabled", True)
        and item.get("resource_url")
        and item.get("resource_url") not in _RESOURCE_PIXMAP_CACHE
    ]
    if not pending:
        return
    for item in pending:
        _item_icon_pixmap(item, load_resource=True)
    if _POPUP is not None:
        _POPUP.update()


def _qt_key_from_name(name):
    text = str(name or "Space").strip()
    lowered = text.lower()
    explicit = {
        "space": QtCore.Qt.Key_Space,
        "spacebar": QtCore.Qt.Key_Space,
        " ": QtCore.Qt.Key_Space,
        "`": QtCore.Qt.Key_QuoteLeft,
        "backtick": QtCore.Qt.Key_QuoteLeft,
        "grave": QtCore.Qt.Key_QuoteLeft,
        "quoteleft": QtCore.Qt.Key_QuoteLeft
    }
    if lowered in explicit:
        return explicit[lowered]
    if len(text) == 1:
        return ord(text.upper())
    normalized = text.replace(" ", "").replace("_", "")
    direct = getattr(QtCore.Qt, "Key_" + normalized, None)
    if direct is not None:
        return direct
    try:
        sequence = QtGui.QKeySequence.fromString(
            text, QtGui.QKeySequence.PortableText)
        if not sequence.isEmpty():
            return sequence[0].key()
    except Exception:
        pass
    return QtCore.Qt.Key_Space


def _qt_modifiers(names):
    mods = QtCore.Qt.NoModifier
    for name in names or []:
        lowered = str(name).lower()
        if lowered in ("ctrl", "control"):
            mods |= QtCore.Qt.ControlModifier
        elif lowered == "shift":
            mods |= QtCore.Qt.ShiftModifier
        elif lowered in ("alt", "option"):
            mods |= QtCore.Qt.AltModifier
    return mods


def _text_input_has_focus():
    widget = QtWidgets.QApplication.focusWidget()
    if widget is None:
        return False
    text_widgets = (
        QtWidgets.QAbstractSpinBox,
        QtWidgets.QComboBox,
        QtWidgets.QLineEdit,
        QtWidgets.QTextEdit,
        QtWidgets.QPlainTextEdit
    )
    if isinstance(widget, text_widgets):
        return True
    try:
        if widget.property("text") is not None or widget.property("placeholderText") is not None:
            return True
    except Exception:
        pass
    try:
        if QtWidgets.QApplication.inputMethod().isVisible():
            return True
    except Exception:
        pass
    class_name = widget.metaObject().className().lower()
    return any(name in class_name for name in ("lineedit", "textedit", "plaintextedit", "spinbox", "combobox"))


def _modifier_bits(modifiers):
    value = getattr(modifiers, "value", modifiers)
    try:
        return int(value)
    except TypeError:
        return int(modifiers)


def _physical_key_down(vk_code):
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk_code) & 0x8000)
    except Exception:
        return False


def _windows_virtual_key(qt_key):
    value = getattr(qt_key, "value", qt_key)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    explicit = {
        int(QtCore.Qt.Key_Backspace): 0x08,
        int(QtCore.Qt.Key_Tab): 0x09,
        int(QtCore.Qt.Key_Return): 0x0D,
        int(QtCore.Qt.Key_Enter): 0x0D,
        int(QtCore.Qt.Key_Escape): 0x1B,
        int(QtCore.Qt.Key_Space): VK_SPACE,
        int(QtCore.Qt.Key_PageUp): 0x21,
        int(QtCore.Qt.Key_PageDown): 0x22,
        int(QtCore.Qt.Key_End): 0x23,
        int(QtCore.Qt.Key_Home): 0x24,
        int(QtCore.Qt.Key_Left): 0x25,
        int(QtCore.Qt.Key_Up): 0x26,
        int(QtCore.Qt.Key_Right): 0x27,
        int(QtCore.Qt.Key_Down): 0x28,
        int(QtCore.Qt.Key_Insert): 0x2D,
        int(QtCore.Qt.Key_Delete): 0x2E,
        int(QtCore.Qt.Key_QuoteLeft): VK_OEM_3
    }
    if value in explicit:
        return explicit[value]
    if 0x30 <= value <= 0x39 or 0x41 <= value <= 0x5A:
        return value
    first_function_key = int(QtCore.Qt.Key_F1)
    last_function_key = int(QtCore.Qt.Key_F24)
    if first_function_key <= value <= last_function_key:
        return 0x70 + value - first_function_key
    if 0x20 <= value <= 0x7E:
        try:
            mapped = int(ctypes.windll.user32.VkKeyScanW(chr(value)))
            if mapped != -1 and (mapped & 0xFFFF) != 0xFFFF:
                return mapped & 0xFF
        except Exception:
            pass
    return 0


def _wheel_hotkey_down(event=None):
    required = QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier
    required_bits = _modifier_bits(required)
    qt_bits = 0
    if event is not None:
        try:
            qt_bits |= _modifier_bits(event.modifiers())
        except Exception:
            pass
    qt_bits |= _modifier_bits(QtWidgets.QApplication.keyboardModifiers())
    qt_match = (qt_bits & required_bits) == required_bits
    physical_match = _physical_key_down(VK_CONTROL) and _physical_key_down(VK_MENU)
    return qt_match or physical_match


def _layer_row_wheel_hotkey_down(event=None):
    required = QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier | QtCore.Qt.ShiftModifier
    required_bits = _modifier_bits(required)
    qt_bits = 0
    if event is not None:
        try:
            qt_bits |= _modifier_bits(event.modifiers())
        except Exception:
            pass
    qt_bits |= _modifier_bits(QtWidgets.QApplication.keyboardModifiers())
    qt_match = (qt_bits & required_bits) == required_bits
    physical_match = (
        _physical_key_down(VK_SHIFT) and
        _physical_key_down(VK_CONTROL) and
        _physical_key_down(VK_MENU)
    )
    return qt_match or physical_match


def _run_wheel_selection(delta, event=None):
    global _LAST_WHEEL_TIME, _LAST_WHEEL_SIGN
    if delta == 0:
        return False
    step = -1 if delta > 0 else 1
    now = time.monotonic()
    sign = 1 if delta > 0 else -1
    if _layer_row_wheel_hotkey_down(event):
        if sign == _LAST_WHEEL_SIGN and now - _LAST_WHEEL_TIME < 0.035:
            return True
        _LAST_WHEEL_TIME = now
        _LAST_WHEEL_SIGN = sign
        _select_relative_layer_row(step)
        return True
    if _wheel_hotkey_down(event):
        if sign == _LAST_WHEEL_SIGN and now - _LAST_WHEEL_TIME < 0.035:
            return True
        _LAST_WHEEL_TIME = now
        _LAST_WHEEL_SIGN = sign
        _select_relative_node(step)
        return True
    return False


def _is_wheel_event(event):
    event_type = event.type()
    wheel_types = [QtCore.QEvent.Wheel]
    graphics_wheel = getattr(QtCore.QEvent, "GraphicsSceneWheel", None)
    if graphics_wheel is not None:
        wheel_types.append(graphics_wheel)
    return event_type in wheel_types


def _wheel_delta(event):
    if hasattr(event, "angleDelta"):
        delta = event.angleDelta().y()
        if delta != 0:
            return delta
    if hasattr(event, "pixelDelta"):
        delta = event.pixelDelta().y()
        if delta != 0:
            return delta
    if hasattr(event, "delta"):
        return event.delta()
    return 0


def _signed_hiword(value):
    high = (int(value) >> 16) & 0xFFFF
    return high - 0x10000 if high & 0x8000 else high


def _native_message_from_pointer(message):
    try:
        return ctypes.wintypes.MSG.from_address(int(message))
    except Exception:
        return None


class NativeWheelFilter(QtCore.QAbstractNativeEventFilter):
    def nativeEventFilter(self, event_type, message):
        try:
            msg = _native_message_from_pointer(message)
            if msg is None:
                return False, 0
            if msg.message in (WM_KEYUP, WM_SYSKEYUP):
                if (
                        _KEY_FILTER is not None
                        and _KEY_FILTER.matches_native_key(int(msg.wParam))):
                    QtCore.QMetaObject.invokeMethod(
                        _KEY_FILTER,
                        "release_hold",
                        QtCore.Qt.QueuedConnection)
                return False, 0
            if _settings_visible() or msg.message not in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
                return False, 0
            delta = _signed_hiword(msg.wParam)
            return (_run_wheel_selection(delta), 0)
        except Exception:
            _error("Native wheel event failed:\n" + traceback.format_exc())
            return False, 0


class HoldKeyFilter(QtCore.QObject):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.reload_config(config)
        self.is_down = False

    def reload_config(self, config):
        shortcut = config.get("shortcut", {})
        self.key = _qt_key_from_name(shortcut.get("key", "Space"))
        self.virtual_key = _windows_virtual_key(self.key)
        self.modifiers = _qt_modifiers(shortcut.get("modifiers", []))
        items_by_id = {
            str(item.get("id", "")): item
            for item in config.get("items", [])
            if item.get("id")
        }
        self.command_shortcuts = []
        for assignment in config.get("command_shortcuts", []):
            if not isinstance(assignment, dict):
                continue
            item = items_by_id.get(str(assignment.get("command_id", "") or ""))
            shortcut_value = assignment.get("shortcut", {})
            if item is None or not isinstance(shortcut_value, dict):
                continue
            key_name = str(shortcut_value.get("key", "") or "")
            action = str(item.get("action", "") or "")
            if not key_name or not action:
                continue
            self.command_shortcuts.append((
                _qt_key_from_name(key_name),
                _qt_modifiers(shortcut_value.get("modifiers", [])),
                action))

    @QtCore.Slot()
    def release_hold(self, run_hovered=True):
        if not self.is_down:
            return False
        self.is_down = False
        if run_hovered and _POPUP is not None and _POPUP.isVisible():
            _POPUP.update_from_global_position()
        _hide_popup(run_hovered=run_hovered)
        return True

    def matches_native_key(self, virtual_key):
        return bool(
            self.is_down
            and self.virtual_key
            and virtual_key == self.virtual_key)

    def eventFilter(self, obj, event):
        if _settings_visible():
            self.release_hold(run_hovered=False)
            return False
        if event.type() == QtCore.QEvent.ApplicationDeactivate:
            self.release_hold(run_hovered=False)
            return False
        if event.type() == QtCore.QEvent.MouseMove and self.is_down:
            if _POPUP is not None and _POPUP.isVisible():
                try:
                    global_position = event.globalPosition()
                except AttributeError:
                    global_position = QtGui.QCursor.pos()
                _POPUP.update_from_global_position(global_position)
        try:
            if _is_wheel_event(event):
                delta = _wheel_delta(event)
                if delta != 0:
                    if hasattr(event, "inverted") and event.inverted():
                        delta = -delta
                    if _run_wheel_selection(delta, event):
                        return True
        except Exception:
            _error("Wheel event failed:\n" + traceback.format_exc())
        if event.type() == QtCore.QEvent.ShortcutOverride:
            if event.key() == self.key and self._modifiers_match(event.modifiers()):
                if _text_input_has_focus():
                    return False
                event.accept()
                return False
            if (not _text_input_has_focus()
                    and self._command_shortcut_action(event)):
                event.accept()
                return False
        if event.type() == QtCore.QEvent.KeyPress:
            if event.isAutoRepeat():
                return False
            if event.key() == QtCore.Qt.Key_H and self._modifiers_match(event.modifiers()):
                if not _text_input_has_focus():
                    _toggle_selected_visibility()
                    return True
            if event.key() == self.key and self._modifiers_match(event.modifiers()):
                if _text_input_has_focus():
                    self.release_hold(run_hovered=False)
                    return False
                self.is_down = True
                _show_popup()
                return True
            if not _text_input_has_focus():
                action = self._command_shortcut_action(event)
                if action:
                    QtCore.QTimer.singleShot(
                        0, lambda selected_action=action:
                        _run_action(selected_action))
                    return True
        elif event.type() == QtCore.QEvent.KeyRelease:
            if event.isAutoRepeat():
                return False
            if event.key() == self.key and self.is_down:
                self.release_hold(run_hovered=True)
                return True
        return False

    def _modifiers_match(self, current):
        watched = QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier | QtCore.Qt.AltModifier
        return (current & watched) == self.modifiers

    def _command_shortcut_action(self, event):
        watched = (
            QtCore.Qt.ControlModifier
            | QtCore.Qt.ShiftModifier
            | QtCore.Qt.AltModifier)
        current_modifiers = event.modifiers() & watched
        for key, modifiers, action in self.command_shortcuts:
            if event.key() == key and current_modifiers == modifiers:
                return action
        return ""

    def _wheel_modifiers_match(self, event):
        return _wheel_hotkey_down(event)


def _reload_config_action():
    global _PAINTER_LANGUAGE_CACHE, _RESOURCE_PRELOAD_RETRIES, _RUNTIME_CONFIG
    _PAINTER_LANGUAGE_CACHE = None
    config = _load_config()
    _RUNTIME_CONFIG = copy.deepcopy(config)
    if _KEY_FILTER is not None:
        _KEY_FILTER.reload_config(config)
    if _POPUP is not None:
        _POPUP.set_config(config)
        _RESOURCE_PRELOAD_RETRIES = 0
        QtCore.QTimer.singleShot(0, _preload_runtime_resource_icons)
    if _SETTINGS_ACTION is not None:
        _SETTINGS_ACTION.setText(_tr("open_settings", config))
    if _TOOLBAR_BUTTON is not None:
        _TOOLBAR_BUTTON.setToolTip(_tr("open_settings", config))
    _log("Config reloaded from " + _config_path())


def _settings_visible():
    return _SETTINGS_DIALOG is not None and _SETTINGS_DIALOG.isVisible()


def _ensure_update_manager():
    global _UPDATE_MANAGER
    if _UPDATE_MANAGER is None:
        parent = substance_painter.ui.get_main_window()
        _UPDATE_MANAGER = UpdateManager(parent)
    return _UPDATE_MANAGER


def _show_settings():
    global _SETTINGS_DIALOG
    if _KEY_FILTER is not None:
        _KEY_FILTER.release_hold(run_hovered=False)
    _hide_popup(run_hovered=False)
    if _SETTINGS_DIALOG is None:
        _SETTINGS_DIALOG = SettingsDialog(substance_painter.ui.get_main_window())
    else:
        _SETTINGS_DIALOG.load_config(_load_config())
    _SETTINGS_DIALOG.show()
    _SETTINGS_DIALOG.raise_()
    _SETTINGS_DIALOG.activateWindow()


def start_plugin():
    global _KEY_FILTER, _NATIVE_WHEEL_FILTER, _POPUP, _RUNTIME_CONFIG
    global _SETTINGS_ACTION, _TOOLBAR_BUTTON, _UPDATE_MANAGER
    main_window = substance_painter.ui.get_main_window()
    config = _load_config()
    _RUNTIME_CONFIG = copy.deepcopy(config)
    app = QtWidgets.QApplication.instance()
    _ensure_packaged_builtin_icons()

    if _KEY_FILTER is None:
        _KEY_FILTER = HoldKeyFilter(config, main_window)
        app.installEventFilter(_KEY_FILTER)

    if _NATIVE_WHEEL_FILTER is None:
        _NATIVE_WHEEL_FILTER = NativeWheelFilter()
        app.installNativeEventFilter(_NATIVE_WHEEL_FILTER)

    if _POPUP is None:
        _POPUP = ToolsPopup(config, main_window)
        _POPUP.prepare()
        QtCore.QTimer.singleShot(1200, _preload_runtime_resource_icons)

    if _SETTINGS_ACTION is None:
        _SETTINGS_ACTION = QtGui.QAction(_tr("open_settings", config), main_window)
        _SETTINGS_ACTION.triggered.connect(_show_settings)
        substance_painter.ui.add_action(sp.ui.ApplicationMenu.Window, _SETTINGS_ACTION)

    if _TOOLBAR_BUTTON is None:
        _TOOLBAR_BUTTON = QtWidgets.QToolButton()
        _TOOLBAR_BUTTON.setObjectName("radialLayerToolsToolbarButton")
        _TOOLBAR_BUTTON.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
        _TOOLBAR_BUTTON.setIcon(_radial_toolbar_icon())
        _TOOLBAR_BUTTON.setIconSize(QtCore.QSize(20, 20))
        _TOOLBAR_BUTTON.setFixedSize(32, 32)
        _TOOLBAR_BUTTON.setAutoRaise(True)
        _TOOLBAR_BUTTON.setFocusPolicy(QtCore.Qt.NoFocus)
        _TOOLBAR_BUTTON.setToolTip(_tr("open_settings", config))
        _TOOLBAR_BUTTON.setAccessibleName(_tr("open_settings", config))
        _TOOLBAR_BUTTON.clicked.connect(_show_settings)
        substance_painter.ui.add_plugins_toolbar_widget(_TOOLBAR_BUTTON)

    update_manager_created = _UPDATE_MANAGER is None
    update_manager = _ensure_update_manager()
    if update_manager_created:
        update_manager.schedule_automatic_check()

    _log("Started hold-key popup build " + PLUGIN_BUILD)


def close_plugin():
    global _KEY_FILTER, _POPUP, _NATIVE_WHEEL_FILTER
    global _RUNTIME_CONFIG, _SETTINGS_ACTION, _SETTINGS_DIALOG, _TOOLBAR_BUTTON
    global _RESOURCE_IMAGE_PROVIDER, _RESOURCE_PRELOAD_RETRIES
    global _RESOURCE_PROVIDER_RETRY_AFTER, _RESOURCE_METADATA_INDEX
    global _RESOURCE_SIDECAR_INDEX, _FILTER_COMMAND_CACHE, _FILTER_SCAN_COMPLETE
    global _UPDATE_MANAGER
    app = QtWidgets.QApplication.instance()
    if app is not None and _KEY_FILTER is not None:
        _KEY_FILTER.release_hold(run_hovered=False)
        app.removeEventFilter(_KEY_FILTER)
        _KEY_FILTER.setParent(None)
        _KEY_FILTER.deleteLater()
        _KEY_FILTER = None
    if app is not None and _NATIVE_WHEEL_FILTER is not None:
        app.removeNativeEventFilter(_NATIVE_WHEEL_FILTER)
        _NATIVE_WHEEL_FILTER = None
    if _POPUP is not None:
        _POPUP.shutdown()
        _POPUP = None
    _RUNTIME_CONFIG = None
    _RESOURCE_IMAGE_PROVIDER = None
    _RESOURCE_PRELOAD_RETRIES = 0
    _RESOURCE_PROVIDER_RETRY_AFTER = 0.0
    _RESOURCE_METADATA_INDEX = None
    _RESOURCE_SIDECAR_INDEX = None
    _FILTER_COMMAND_CACHE = None
    _FILTER_SCAN_COMPLETE = False
    _ICON_PIXMAP_CACHE.clear()
    _RESOURCE_PIXMAP_CACHE.clear()
    if _SETTINGS_DIALOG is not None:
        _SETTINGS_DIALOG.shutdown()
        _SETTINGS_DIALOG.close()
        _SETTINGS_DIALOG.setParent(None)
        _SETTINGS_DIALOG.deleteLater()
        _SETTINGS_DIALOG = None
    if _UPDATE_MANAGER is not None:
        _UPDATE_MANAGER.shutdown()
        _UPDATE_MANAGER.setParent(None)
        _UPDATE_MANAGER.deleteLater()
        _UPDATE_MANAGER = None
    if _SETTINGS_ACTION is not None:
        try:
            _SETTINGS_ACTION.triggered.disconnect(_show_settings)
        except Exception:
            pass
        substance_painter.ui.delete_ui_element(_SETTINGS_ACTION)
        _SETTINGS_ACTION = None
    if _TOOLBAR_BUTTON is not None:
        try:
            _TOOLBAR_BUTTON.clicked.disconnect(_show_settings)
        except Exception:
            pass
        substance_painter.ui.delete_ui_element(_TOOLBAR_BUTTON)
        _TOOLBAR_BUTTON = None
    if app is not None:
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        app.processEvents()
    _log("Closed.")
