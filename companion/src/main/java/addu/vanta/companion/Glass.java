package addu.vanta.companion;

import net.minecraft.client.gui.DrawContext;

/** Dark-purple / silver glass drawing helpers. */
public final class Glass {
    private Glass() {}
    public static final int TEXT = 0xFFE6E6F0, DIM = 0xFF9C98B0, ACCENT = 0xFFB98CFF, SILVER = 0xFFC9CAD6, OK = 0xFF8CFFB4, WARN = 0xFFFFC27A;

    public static void panel(DrawContext c, int x1, int y1, int x2, int y2) {
        c.fillGradient(x1, y1, x2, y2, 0xC8241238, 0xD0100818);   // smoky purple glass
        c.fillGradient(x1 + 1, y1 + 1, x2 - 1, y1 + (y2 - y1) / 3, 0x22FFFFFF, 0x00FFFFFF); // top sheen
        c.drawStrokedRectangle(x1, y1, x2 - x1, y2 - y1, 0x99C9CAD6); // silver rim
        c.fill(x1 + 1, y1 + 1, x2 - 1, y1 + 2, 0x55FFFFFF);
    }

    public static void button(DrawContext c, int x, int y, int w, int h, boolean hover, boolean active, boolean enabled) {
        int top = active ? 0xE07A4CC0 : hover ? 0xB0503080 : 0x90301A4C;
        int bot = active ? 0xE04A2A80 : hover ? 0xB0301A50 : 0x90180C28;
        if (!enabled) { top = 0x60201828; bot = 0x60100C14; }
        c.fillGradient(x, y, x + w, y + h, top, bot);
        c.fill(x + 1, y + 1, x + w - 1, y + 2, hover ? 0x66FFFFFF : 0x33FFFFFF);
        c.drawStrokedRectangle(x, y, w, h, active ? 0xDDE0D0FF : hover ? 0xBBC9CAD6 : 0x66C9CAD6);
    }
}
