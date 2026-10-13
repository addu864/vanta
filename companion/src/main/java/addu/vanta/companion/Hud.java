package addu.vanta.companion;

import net.minecraft.client.MinecraftClient;
import net.minecraft.client.font.TextRenderer;
import net.minecraft.client.gui.DrawContext;
import net.minecraft.entity.EquipmentSlot;
import net.minecraft.entity.effect.StatusEffectInstance;
import net.minecraft.item.ItemStack;
import net.minecraft.text.Text;

import java.util.ArrayList;
import java.util.List;

public final class Hud {
    private Hud() {}
    public static final String[] IDS = {"fps", "coords", "armor", "potions"};
    public static final int SILVER = 0xFFDADBE6, PURPLE = 0xFFB98CFF;

    public static boolean enabled(String id) {
        VantaConfig c = VantaConfig.get();
        return switch (id) { case "fps" -> c.showFps; case "coords" -> c.showCoords; case "armor" -> c.showArmor; default -> c.showPotions; };
    }

    public static void toggle(String id) {
        VantaConfig c = VantaConfig.get();
        switch (id) { case "fps" -> c.showFps = !c.showFps; case "coords" -> c.showCoords = !c.showCoords; case "armor" -> c.showArmor = !c.showArmor; default -> c.showPotions = !c.showPotions; }
        c.save();
    }

    public static String label(String id) {
        return switch (id) { case "fps" -> "FPS counter"; case "coords" -> "Coordinates"; case "armor" -> "Armor status"; default -> "Potion effects"; };
    }

    /** Lines for an element; preview=true gives placeholder content for the editor. */
    public static List<String> lines(String id, boolean preview) {
        MinecraftClient mc = MinecraftClient.getInstance();
        List<String> l = new ArrayList<>();
        switch (id) {
            case "fps" -> l.add("FPS " + mc.getCurrentFps());
            case "coords" -> {
                if (mc.player != null) {
                    l.add(String.format("XYZ %.1f / %.1f / %.1f", mc.player.getX(), mc.player.getY(), mc.player.getZ()));
                    l.add("Facing " + mc.player.getHorizontalFacing().asString());
                } else if (preview) { l.add("XYZ 128.5 / 64.0 / -42.5"); l.add("Facing north"); }
            }
            case "potions" -> {
                if (mc.player != null) for (StatusEffectInstance e : mc.player.getStatusEffects()) {
                    if (!e.shouldShowIcon() && !e.shouldShowParticles()) continue;
                    int s = e.getDuration() / 20;
                    String time = e.isInfinite() ? "∞" : String.format("%d:%02d", s / 60, s % 60);
                    String amp = e.getAmplifier() > 0 ? " " + (e.getAmplifier() + 1) : "";
                    l.add(Text.translatable(e.getEffectType().value().getTranslationKey()).getString() + amp + "  " + time);
                }
                if (l.isEmpty() && preview) { l.add("Speed 2  1:30"); l.add("Night Vision  8:00"); }
            }
            default -> {}
        }
        return l;
    }

    public static int[] size(String id, TextRenderer tr, boolean preview) {
        if (id.equals("armor")) return new int[]{80, 4 * 17};
        int w = 0; List<String> l = lines(id, preview);
        for (String s : l) w = Math.max(w, tr.getWidth(s));
        return new int[]{Math.max(w + 6, 40), Math.max(l.size(), 1) * 10 + 2};
    }

    public static void renderElement(DrawContext ctx, String id, int x, int y, boolean preview) {
        MinecraftClient mc = MinecraftClient.getInstance();
        TextRenderer tr = mc.textRenderer;
        if (id.equals("armor")) {
            EquipmentSlot[] slots = {EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET};
            int row = 0;
            for (EquipmentSlot s : slots) {
                ItemStack st = mc.player != null ? mc.player.getEquippedStack(s) : ItemStack.EMPTY;
                int yy = y + row * 17; row++;
                if (st.isEmpty()) { if (preview) ctx.drawTextWithShadow(tr, "— " + s.getName(), x + 2, yy + 4, 0x99DADBE6); continue; }
                ctx.drawItem(st, x, yy);
                if (st.isDamageable()) {
                    int left = st.getMaxDamage() - st.getDamage();
                    float f = left / (float) st.getMaxDamage();
                    int col = f > 0.5f ? SILVER : f > 0.2f ? 0xFFFFD27A : 0xFFFF6B6B;
                    ctx.drawTextWithShadow(tr, left + "/" + st.getMaxDamage(), x + 19, yy + 4, col);
                }
            }
            return;
        }
        int i = 0;
        for (String s : lines(id, preview)) ctx.drawTextWithShadow(tr, s, x + 3, y + 2 + 10 * i++, i == 1 ? SILVER : PURPLE);
    }

    public static void render(DrawContext ctx) {
        MinecraftClient mc = MinecraftClient.getInstance();
        if (mc.options.hudHidden || mc.getDebugHud().shouldShowDebugHud() || mc.currentScreen instanceof HudEditScreen) return;
        for (String id : IDS) {
            if (!enabled(id)) continue;
            VantaConfig.Pos p = VantaConfig.get().hudPositions.get(id);
            renderElement(ctx, id, p.x, p.y, false);
        }
    }
}
