package addu.vanta.companion;

import net.minecraft.client.gui.Click;
import net.minecraft.client.gui.DrawContext;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.text.Text;

public class HudEditScreen extends Screen {
    private final Screen parent;
    private String dragging; private double offX, offY;

    public HudEditScreen(Screen parent) { super(Text.literal("Vanta HUD editor")); this.parent = parent; }

    @Override public void renderBackground(DrawContext c, int mx, int my, float d) { c.fill(0, 0, width, height, 0x60100818); }

    @Override public void render(DrawContext c, int mx, int my, float d) {
        super.render(c, mx, my, d);
        c.drawCenteredTextWithShadow(textRenderer, "Drag HUD elements · Esc to save", width / 2, height - 20, Glass.SILVER);
        for (String id : Hud.IDS) {
            VantaConfig.Pos p = VantaConfig.get().hudPositions.get(id);
            int[] s = Hud.size(id, textRenderer, true);
            boolean on = Hud.enabled(id);
            c.fill(p.x - 1, p.y - 1, p.x + s[0] + 1, p.y + s[1] + 1, id.equals(dragging) ? 0x807A4CC0 : 0x50301A4C);
            c.drawStrokedRectangle(p.x - 1, p.y - 1, s[0] + 2, s[1] + 2, on ? 0xCCC9CAD6 : 0x55C9CAD6);
            Hud.renderElement(c, id, p.x, p.y, true);
            if (!on) c.drawTextWithShadow(textRenderer, "(hidden)", p.x + 2, p.y + s[1] + 2, Glass.DIM);
        }
    }

    @Override public boolean mouseClicked(Click click, boolean doubled) {
        for (String id : Hud.IDS) {
            VantaConfig.Pos p = VantaConfig.get().hudPositions.get(id);
            int[] s = Hud.size(id, textRenderer, true);
            if (click.x() >= p.x && click.x() < p.x + s[0] && click.y() >= p.y && click.y() < p.y + s[1]) {
                dragging = id; offX = click.x() - p.x; offY = click.y() - p.y; return true;
            }
        }
        return super.mouseClicked(click, doubled);
    }

    @Override public boolean mouseDragged(Click click, double dx, double dy) {
        if (dragging == null) return false;
        VantaConfig.Pos p = VantaConfig.get().hudPositions.get(dragging);
        int[] s = Hud.size(dragging, textRenderer, true);
        p.x = (int) Math.max(0, Math.min(width - s[0], click.x() - offX));
        p.y = (int) Math.max(0, Math.min(height - s[1], click.y() - offY));
        return true;
    }

    @Override public boolean mouseReleased(Click click) { dragging = null; VantaConfig.get().save(); return true; }
    @Override public void close() { VantaConfig.get().save(); client.setScreen(parent); }
}
