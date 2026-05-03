import io
import os
import random
import requests
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from typing import List, Tuple, Optional
from datetime import datetime


class RankingGenerator:
    # 颜色配置
    COLORS = {
        "bg": (15, 18, 28),           # 深蓝灰背景（调暗）
        "card_bg": (25, 30, 45),       # 卡片背景
        "bar_start": (255, 255, 255),  # 渐变起点-白色
        "bar_end": (255, 182, 193),    # 渐变终点-淡粉色
        "text": (255, 255, 255),       # 白色文字
        "subtext": (140, 145, 160),    # 灰色副文字
        "rank_gold": (255, 215, 0),    # 金色（第1名）
        "rank_silver": (192, 192, 192), # 银色（第2名）
        "rank_bronze": (205, 127, 50), # 铜色（第3名）
    }

    # 排行榜配置
    ITEM_HEIGHT = 28      # 每个条目高度（更紧凑）
    PADDING = 12          # 内边距
    HEADER_HEIGHT = 60     # 标题区域高度
    AVATAR_SIZE = 18      # 头像大小（再减小）
    IMAGE_SIZE = 650       # 正方形图片尺寸
    BAR_HEIGHT = 8        # 进度条高度
    BAR_GAP = 2           # 条目内间距

    def __init__(self, bg_dir: str = "data/backgrounds"):
        self.bg_dir = bg_dir
        self.font_cache = {}
        self._bg_cache = None
        self._executor = ThreadPoolExecutor(max_workers=8)
        self._is_shutdown = False

    def shutdown(self):
        """关闭线程池，释放资源"""
        if not self._is_shutdown:
            self._executor.shutdown(wait=False)
            self._is_shutdown = True
        self._avatar_cache = {}
        self._cache_lock = None  # 简化：dict操作本身在单线程内是原子的

    def _get_executor(self):
        """获取线程池（如已 shutdown 则报错）"""
        if self._is_shutdown:
            raise RuntimeError("RankingGenerator 已关闭，无法继续使用")
        return self._executor

    def _sync_download_avatar(self, qq号: str) -> Image.Image:
        """同步下载头像（在线程池中执行），下载后强制裁剪为正方形"""
        cache_key = qq号
        if cache_key in self._avatar_cache:
            return self._avatar_cache[cache_key]

        try:
            url = f"https://q1.qlogo.cn/headimg_dl?dst_uin={qq号}&spec=100"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                avatar = Image.open(io.BytesIO(response.content)).convert("RGBA")
                # 强制裁剪为正方形
                avatar = self._force_square_avatar(avatar)
                self._avatar_cache[cache_key] = avatar
                return avatar
        except Exception:
            pass

        default = Image.new("RGBA", (self.AVATAR_SIZE, self.AVATAR_SIZE), (100, 100, 120, 255))
        self._avatar_cache[cache_key] = default
        return default

    def get_font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        """获取字体（带缓存）"""
        key = (size, bold)
        if key not in self.font_cache:
            try:
                self.font_cache[key] = ImageFont.truetype("msyh.ttc", size)
            except Exception:
                self.font_cache[key] = ImageFont.load_default()
        return self.font_cache[key]

    def round_corners(self, img: Image.Image, radius: int) -> Image.Image:
        """将图片变成圆角（确保输出尺寸不变）"""
        # 确保图片尺寸正确
        img = img.resize((self.AVATAR_SIZE, self.AVATAR_SIZE), Image.Resampling.LANCZOS)

        mask = Image.new("L", (self.AVATAR_SIZE, self.AVATAR_SIZE), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle([(0, 0), (self.AVATAR_SIZE, self.AVATAR_SIZE)], radius=radius, fill=255)

        output = Image.new("RGBA", (self.AVATAR_SIZE, self.AVATAR_SIZE), (0, 0, 0, 0))
        output.paste(img, (0, 0), mask)
        return output

    def create_gradient_bar(self, width: int, height: int) -> Image.Image:
        """创建渐变柱状条"""
        bar = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(bar)
        for x in range(width):
            ratio = x / width if width > 0 else 0
            r = int(self.COLORS["bar_start"][0] + (self.COLORS["bar_end"][0] - self.COLORS["bar_start"][0]) * ratio)
            g = int(self.COLORS["bar_start"][1] + (self.COLORS["bar_end"][1] - self.COLORS["bar_start"][1]) * ratio)
            b = int(self.COLORS["bar_start"][2] + (self.COLORS["bar_end"][2] - self.COLORS["bar_start"][2]) * ratio)
            draw.line([(x, 0), (x, height)], fill=(r, g, b))
        return bar

    def create_rounded_bar(self, width: int, height: int, alpha: int = 64) -> Image.Image:
        """创建半透明渐变进度条"""
        bar = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(bar)

        # 淡紫色
        r, g, b = 200, 180, 255

        # 从左到右透明度从0%渐变到60%
        max_alpha = int(255 * 0.6)  # 60%
        for x in range(width):
            # 左边x=0透明度0，右边x=width-1透明度60%
            ratio = x / max(width - 1, 1)
            current_alpha = int(max_alpha * ratio)
            draw.rectangle([(x, 0), (x, height - 1)], fill=(r, g, b, current_alpha))

        return bar

    def get_random_background(self, size: Tuple[int, int]) -> Image.Image:
        """随机获取一张背景图，裁切到目标尺寸、高斯模糊20%并调暗30%"""
        # 获取所有支持的图片文件
        supported_ext = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.gif'}
        images = []

        if os.path.exists(self.bg_dir):
            for f in os.listdir(self.bg_dir):
                ext = os.path.splitext(f.lower())[1]
                if ext in supported_ext:
                    images.append(os.path.join(self.bg_dir, f))

        if not images:
            return Image.new("RGB", size, self.COLORS["bg"])

        # 随机选择一张
        chosen = random.choice(images)

        try:
            bg = Image.open(chosen).convert("RGB")

            # 裁切到目标尺寸（居中裁切，保留主要内容）
            bg_width, bg_height = bg.size
            target_width, target_height = size

            # 计算裁切区域
            bg_ratio = bg_width / bg_height
            target_ratio = target_width / target_height

            if bg_ratio > target_ratio:
                # 图片太宽，以高度为基准裁切宽度
                new_width = int(bg_height * target_ratio)
                left = (bg_width - new_width) // 2
                top = 0
                right = left + new_width
                bottom = bg_height
            else:
                # 图片太高，以宽度为基准裁切高度
                new_height = int(bg_width / target_ratio)
                top = (bg_height - new_height) // 2
                left = 0
                right = bg_width
                bottom = top + new_height

            bg = bg.crop((left, top, right, bottom))
            bg = bg.resize(size, Image.Resampling.LANCZOS)

            # 高斯模糊20%（radius=5效果接近20%模糊程度）
            bg = bg.filter(ImageFilter.GaussianBlur(radius=5))

            # 调暗30%：在原图上叠加黑色半透明层
            dark_overlay = Image.new("RGB", size, (0, 0, 0))
            bg = Image.blend(bg, dark_overlay, 0.30)

            return bg

        except Exception:
            # 失败时返回调暗的纯色背景
            fallback = Image.new("RGB", size, self.COLORS["bg"])
            dark_overlay = Image.new("RGB", size, (0, 0, 0))
            return Image.blend(fallback, dark_overlay, 0.30)

    def _force_square_avatar(self, avatar: Image.Image) -> Image.Image:
        """强制将头像裁剪为正方形（从中心裁切）并缩放到目标尺寸"""
        width, height = avatar.size

        # 裁剪为正方形（从中心裁切）
        if width != height:
            size = min(width, height)
            left = (width - size) // 2
            top = (height - size) // 2
            avatar = avatar.crop((left, top, left + size, top + size))

        # 缩放到目标尺寸
        avatar = avatar.resize((self.AVATAR_SIZE, self.AVATAR_SIZE), Image.Resampling.LANCZOS)
        return avatar

    def _get_contrast_color(self, bg_brightness: float) -> tuple:
        """根据背景亮度智能选择对比度强的文字颜色"""
        # 背景亮度低于128用白色，高于128用黑色
        if bg_brightness < 128:
            return (255, 255, 255, 255)  # 白色
        else:
            return (0, 0, 0, 255)  # 黑色

    def generate_ranking_image(
        self,
        stats: List[Tuple[str, int, str]],
        group_id: str = ""
    ) -> bytes:
        """生成排行榜图片

        布局逻辑（从上到下每行）：
        [排名] [头像] [昵称 xxxxxxxx] [========进度条========] [xx条] [xx%]

        Args:
            stats: [(QQ号, 发言次数, 昵称), ...] 列表，按发言次数降序
            group_id: 群号

        Returns:
            PNG格式的图片字节数据
        """
        if not stats:
            stats = [("10001", 0, "测试用户")]

        stats = stats[:15]

        # 计算自适应条目高度
        content_height = self.HEADER_HEIGHT + len(stats) * self.ITEM_HEIGHT + self.PADDING
        img_size = (self.IMAGE_SIZE, self.IMAGE_SIZE)

        if content_height > self.IMAGE_SIZE:
            available = self.IMAGE_SIZE - self.PADDING - self.HEADER_HEIGHT
            effective_item_height = available // len(stats)
        else:
            effective_item_height = self.ITEM_HEIGHT

        # 随机获取背景图并裁切到正方形
        img = self.get_random_background(img_size)
        img = img.convert("RGBA")

        # 计算背景亮度（用于智能文字颜色）
        bg_stats = img.getcolors(maxcolors=256)
        if bg_stats:
            dominant_color = max(bg_stats, key=lambda x: x[0])[1]
            bg_brightness = sum(dominant_color[:3]) / 3
        else:
            bg_brightness = 30

        contrast_rgb = self._get_contrast_color(bg_brightness)[:3]

        draw = ImageDraw.Draw(img)

        # 标题
        today = datetime.now().strftime("%Y-%m-%d")
        title = "今日说话排行榜"
        try:
            title_font = self.get_font(24, bold=True)
            sub_font = self.get_font(13)
        except Exception:
            title_font = self.get_font(20)
            sub_font = self.get_font(11)

        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        draw.text(((self.IMAGE_SIZE - (title_bbox[2] - title_bbox[0])) // 2, 8), title, font=title_font, fill=contrast_rgb)

        sub_bbox = draw.textbbox((0, 0), today, font=sub_font)
        draw.text(((self.IMAGE_SIZE - (sub_bbox[2] - sub_bbox[0])) // 2, 35), today, font=sub_font, fill=contrast_rgb)

        # 分隔线
        draw.line([(15, 55), (self.IMAGE_SIZE - 15, 55)], fill=contrast_rgb, width=1)

        # 统计数据
        max_count = max(count for _, count, _ in stats) if stats else 1
        total_count = sum(count for _, count, _ in stats)

        # 并行下载所有头像
        executor = self._get_executor()
        avatar_images = list(executor.map(
            lambda qq: self._sync_download_avatar(qq),
            [qq for qq, _, _ in stats]
        ))

        # 布局常量
        RANK_W = 22       # 排名区域宽度
        AVATAR_W = self.AVATAR_SIZE + 4  # 头像区域宽度
        NAME_W = 65       # 昵称区域宽度
        PCT_W = 40        # 百分比区域宽度
        MARGIN = 8        # 右边距
        BAR_Y_OFFSET = 15  # 进度条Y偏移（从条目顶部算）
        BAR_TOTAL_W = self.IMAGE_SIZE - RANK_W - AVATAR_W - NAME_W - PCT_W - MARGIN - 8

            # 绘制每个条目
        for i, (qq号, count, 昵称) in enumerate(stats):
            y = self.HEADER_HEIGHT + i * effective_item_height

            # 排名
            rank_color = self.COLORS["rank_gold"] if i == 0 else (
                self.COLORS["rank_silver"] if i == 1 else (
                    self.COLORS["rank_bronze"] if i == 2 else contrast_rgb
                )
            )
            try:
                rank_font = self.get_font(10, bold=True)
            except Exception:
                rank_font = self.get_font(8)
            draw.text((4, y + 3), f"#{i + 1}", font=rank_font, fill=rank_color)

            # 头像
            avatar = self.round_corners(avatar_images[i], self.AVATAR_SIZE // 2)
            img.paste(avatar, (RANK_W, y + 2), avatar)

            # 昵称
            try:
                name_font = self.get_font(10)
            except Exception:
                name_font = self.get_font(8)
            name_text = (昵称 if 昵称 else qq号)[:5]
            draw.text((RANK_W + AVATAR_W, y + 3), name_text, font=name_font, fill=contrast_rgb)

            # 进度条（紧跟昵称）
            bar_w = int((count / max_count) * BAR_TOTAL_W)
            bar_y = y + BAR_Y_OFFSET
            if bar_w > 0:
                bar_img = self.create_rounded_bar(bar_w, self.BAR_HEIGHT, alpha=80)
                img.paste(bar_img, (RANK_W + AVATAR_W + NAME_W, bar_y), bar_img)

            # 条数（紧跟进度条）
            count_text = f"{count}条"
            try:
                count_font = self.get_font(8)
            except Exception:
                count_font = self.get_font(7)
            count_bbox = draw.textbbox((0, 0), count_text, font=count_font)
            count_w = count_bbox[2] - count_bbox[0]
            if bar_w > count_w:
                draw.text((RANK_W + AVATAR_W + NAME_W + bar_w + 2, y + BAR_Y_OFFSET), count_text, font=count_font, fill=contrast_rgb)

            # 百分比
            pct = f"{(count / total_count * 100):.1f}%" if total_count > 0 else "0%"
            try:
                pct_font = self.get_font(9)
            except Exception:
                pct_font = self.get_font(7)
            pct_bbox = draw.textbbox((0, 0), pct, font=pct_font)
            draw.text((self.IMAGE_SIZE - (pct_bbox[2] - pct_bbox[0]) - 6, y + 3), pct, font=pct_font, fill=contrast_rgb)

        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()

    def generate_summary_image(
        self,
        title: str,
        content_lines: List[str],
        footer: str = ""
    ) -> bytes:
        """生成总结图片（优化版式，动态高度）"""
        LINE_HEIGHT = 28
        SECTION_GAP = 8
        PADDING = 30
        TITLE_SIZE = 22
        CONTENT_SIZE = 15
        FOOTER_SIZE = 12
        SECTION_HEADER_SIZE = 14

        ACCENT_COLOR = (100, 149, 237)
        SUBTEXT_COLOR = (140, 145, 160)
        IMG_WIDTH = 650

        # 先创建临时画布，用于精确计算换行后的实际高度
        measure_img = Image.new("RGB", (IMG_WIDTH, 10), self.COLORS["bg"])
        measure_draw = ImageDraw.Draw(measure_img)

        try:
            title_font = self.get_font(TITLE_SIZE, bold=True)
        except Exception:
            title_font = self.get_font(TITLE_SIZE)

        try:
            content_font = self.get_font(CONTENT_SIZE)
            section_font = self.get_font(SECTION_HEADER_SIZE, bold=True)
        except Exception:
            content_font = self.get_font(CONTENT_SIZE)
            section_font = self.get_font(SECTION_HEADER_SIZE)

        TITLE_HEIGHT = 50
        FOOTER_HEIGHT = 30 if footer else 0
        FOOTER_Y_GAP = 20 if footer else 0
        max_width = IMG_WIDTH - 2 * PADDING

        # 精确计算内容高度
        content_height = 0
        for line in content_lines:
            if not line.strip():
                content_height += SECTION_GAP
                continue

            is_section_header = (
                line.strip() and
                not line.startswith("  ") and
                not line.startswith("#") and
                ":" in line and
                len(line) < 40
            )

            if is_section_header:
                content_height += LINE_HEIGHT
            else:
                line_text = line.strip() if line.startswith("  ") else line
                content_height += self._measure_wrapped_text_height(measure_draw, line_text, max_width, content_font, LINE_HEIGHT)

        total_height = PADDING + TITLE_HEIGHT + 10 + 15 + content_height + FOOTER_Y_GAP + FOOTER_HEIGHT + PADDING

        img = Image.new("RGB", (IMG_WIDTH, total_height), self.COLORS["bg"])
        draw = ImageDraw.Draw(img)

        bg_brightness = 30
        contrast_rgb = self._get_contrast_color(bg_brightness)[:3]

        title_bbox = draw.textbbox((0, 0), title, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        draw.text(((IMG_WIDTH - title_width) // 2, PADDING), title, font=title_font, fill=contrast_rgb)

        line_y = PADDING + TITLE_HEIGHT + 10
        draw.line([(PADDING, line_y), (IMG_WIDTH - PADDING, line_y)], fill=contrast_rgb, width=1)

        y = line_y + 15

        for line in content_lines:
            if not line.strip():
                y += SECTION_GAP
                continue

            is_section_header = (
                line.strip() and
                not line.startswith("  ") and
                not line.startswith("#") and
                ":" in line and
                len(line) < 40
            )

            if is_section_header:
                draw.text((PADDING, y), line.strip(), font=section_font, fill=ACCENT_COLOR)
                y += LINE_HEIGHT
            else:
                line_text = line.strip() if line.startswith("  ") else line
                used_height = self._draw_wrapped_text(draw, line_text, PADDING, y, max_width, content_font, contrast_rgb, LINE_HEIGHT)
                y += used_height

        if footer:
            try:
                footer_font = self.get_font(FOOTER_SIZE)
            except Exception:
                footer_font = self.get_font(FOOTER_SIZE)
            footer_bbox = draw.textbbox((0, 0), footer, font=footer_font)
            footer_width = footer_bbox[2] - footer_bbox[0]
            draw.text(((IMG_WIDTH - footer_width) // 2, y + 10), footer, font=footer_font, fill=SUBTEXT_COLOR)

        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()

    def _measure_wrapped_text_height(self, draw, text: str, max_width: int, font, line_height: int) -> int:
        """测量自动换行文本高度"""
        if not text:
            return 0

        lines = 1
        current_line = ""
        for char in text:
            test_line = current_line + char
            test_bbox = draw.textbbox((0, 0), test_line, font=font)
            if test_bbox[2] - test_bbox[0] > max_width:
                lines += 1
                current_line = char
            else:
                current_line = test_line
        return lines * line_height

    def _draw_wrapped_text(self, draw, text: str, x: int, y: int, max_width: int, font, fill, line_height: int = 28):
        """绘制自动换行文本，返回实际占用高度"""
        if not text:
            return 0

        current_line = ""
        lines = 0
        for char in text:
            test_line = current_line + char
            test_bbox = draw.textbbox((0, 0), test_line, font=font)
            if test_bbox[2] - test_bbox[0] > max_width:
                draw.text((x, y), current_line, font=font, fill=fill)
                y += line_height
                lines += 1
                current_line = char
            else:
                current_line = test_line

        if current_line:
            draw.text((x, y), current_line, font=font, fill=fill)
            lines += 1

        return lines * line_height


def generate_ranking_image(
    stats: List[Tuple[str, int, str]],
    group_id: str = ""
) -> bytes:
    """便捷函数：生成排行榜图片"""
    generator = RankingGenerator()
    return generator.generate_ranking_image(stats, group_id)


def generate_summary_image(
    title: str,
    content_lines: list,
    footer: str = ""
) -> bytes:
    """生成总结图片"""
    generator = RankingGenerator()
    return generator.generate_summary_image(title, content_lines, footer)
