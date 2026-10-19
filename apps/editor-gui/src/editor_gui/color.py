from imgui_bundle import imgui


def color_u32(r: float, g: float, b: float, a: float = 1.0) -> int:
    return imgui.get_color_u32(imgui.ImVec4(r, g, b, a))


def color_from_vec4(c: imgui.ImVec4) -> int:
    return imgui.get_color_u32(c)


def warning_red() -> imgui.ImVec4:
    """Red for warning text: light on dark themes, darker where the text is dark (light themes)."""
    text = imgui.get_style_color_vec4(imgui.Col_.text)
    if 0.299 * text.x + 0.587 * text.y + 0.114 * text.z < 0.5:
        return imgui.ImVec4(0.78, 0.1, 0.1, 1.0)
    return imgui.ImVec4(1.0, 0.4, 0.4, 1.0)
