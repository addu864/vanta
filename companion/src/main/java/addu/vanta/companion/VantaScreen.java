package addu.vanta.companion;

import net.minecraft.client.gui.Click;
import net.minecraft.client.gui.DrawContext;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.input.KeyInput;
import net.minecraft.resource.ResourcePackProfile;
import net.minecraft.text.Text;
import net.minecraft.util.Util;

import java.util.ArrayList;
import java.util.List;
import java.util.function.BooleanSupplier;
import java.util.function.Supplier;

public class VantaScreen extends Screen {
    public enum Tab { MODS("Mods"), VISUALS("Visuals"), HUD("HUD"), PERFORMANCE("Performance"), FULLBRIGHT("Fullbright");
        final String label; Tab(String l) { label = l; } }

    private record Btn(int x, int y, int w, int h, Supplier<String> label, Runnable action, BooleanSupplier active, boolean enabled, boolean scroll) {}
    private record Line(int x, int y, Supplier<String> text, int color, boolean scroll) {}

    private static Tab lastTab = Tab.MODS;
    private final Screen parent;
    private Tab tab = lastTab;
    private final List<Btn> buttons = new ArrayList<>();
    private final List<Line> lines = new ArrayList<>();
    private int px1, py1, px2, py2, cy1, cy2, scroll, contentH;
    private String status = "";
    private List<ModsManager.Entry> mods;

    public VantaScreen(Screen parent) { super(Text.literal("Vanta Companion")); this.parent = parent; }
    public static VantaScreen at(Screen parent, Tab t) { lastTab = t; return new VantaScreen(parent); }

    @Override protected void init() { rebuild(); }

    private void rebuild() {
        buttons.clear(); lines.clear();
        int pw = Math.min(420, width - 20), ph = Math.min(280, height - 20);
        px1 = (width - pw) / 2; py1 = (height - ph) / 2; px2 = px1 + pw; py2 = py1 + ph;
        int tw = (pw - 16 - 4 * 4) / 5;
        for (int i = 0; i < Tab.values().length; i++) {
            Tab t = Tab.values()[i];
            buttons.add(new Btn(px1 + 8 + i * (tw + 4), py1 + 24, tw, 18, () -> t.label, () -> { tab = t; lastTab = t; scroll = 0; status = ""; rebuild(); }, () -> tab == t, true, false));
        }
        cy1 = py1 + 50; cy2 = py2 - 22;
        int x = px1 + 10, w = pw - 20, y = cy1 + 4;
        switch (tab) {
            case MODS -> y = buildMods(x, y, w);
            case VISUALS -> y = buildVisuals(x, y, w);
            case HUD -> y = buildHud(x, y, w);
            case PERFORMANCE -> y = buildPerf(x, y, w);
            case FULLBRIGHT -> y = buildFullbright(x, y, w);
        }
        contentH = y - cy1;
        scroll = Math.max(0, Math.min(scroll, Math.max(0, contentH - (cy2 - cy1))));
    }

    private void line(int x, int y, String s, int c) { lines.add(new Line(x, y, () -> s, c, true)); }
    private void btn(int x, int y, int w, String label, Runnable r, BooleanSupplier active) { buttons.add(new Btn(x, y, w, 16, () -> label, r, active, true, true)); }
    private void btn(int x, int y, int w, Supplier<String> label, Runnable r, BooleanSupplier active, boolean en) { buttons.add(new Btn(x, y, w, 16, label, r, active, en, true)); }

    private int buildMods(int x, int y, int w) {
        if (mods == null) mods = ModsManager.list();
        line(x, y + 4, mods.size() + " mods  ·  changes apply on next restart", Glass.DIM);
        btn(x + w - 90, y, 90, "Open folder", () -> Util.getOperatingSystem().open(ModsManager.modsDir().toFile()), () -> false);
        y += 22;
        for (ModsManager.Entry e : mods) {
            String state = e.pending() ? (e.enabledNext ? "  → on after restart" : "  → off after restart") : "";
            lines.add(new Line(x + 2, y + 4, () -> e.name + " §7" + e.version + "§e" + (e.pending() ? (e.enabledNext ? "  → on after restart" : "  → off after restart") : ""), e.enabledNext ? Glass.TEXT : Glass.DIM, true));
            boolean cfg = e.loadedNow && Compat.hasConfigScreen(e.id);
            if (cfg) btn(x + w - 128, y, 54, "Config", () -> { Screen s = Compat.configScreen(e.id, this); if (s != null) client.setScreen(s); else status = "Config screen failed"; }, () -> false);
            btn(x + w - 70, y, 70, () -> e.enabledNext ? "Enabled" : "Disabled", () -> {
                String err = ModsManager.setEnabled(e, !e.enabledNext);
                status = err == null ? e.name + (e.enabledNext ? " will load" : " will not load") + " next launch" : e.name + ": " + err;
            }, () -> e.enabledNext, e.file != null && !e.id.equals(VantaCompanion.MOD_ID));
            y += 19;
        }
        if (!Compat.loaded("modmenu")) { line(x, y + 4, "Install Mod Menu to open mod config screens from here.", Glass.DIM); y += 16; }
        return y;
    }

    private int buildVisuals(int x, int y, int w) {
        line(x, y, "Shaders", Glass.ACCENT); y += 14;
        if (Compat.irisAvailable()) btn(x, y, 160, () -> "Shaders: " + (Compat.shadersEnabled() ? "ON" : "OFF"), () -> { Compat.setShaders(!Compat.shadersEnabled()); }, Compat::shadersEnabled, true);
        else btn(x, y, 160, () -> "Shaders: Iris not installed", () -> {}, () -> false, false);
        btn(x + 166, y, w - 166, () -> "Trailer style: " + (VantaConfig.get().trailerStyle ? "ON" : "OFF"), () -> { status = Features.setTrailerStyle(!VantaConfig.get().trailerStyle); }, () -> VantaConfig.get().trailerStyle, true);
        y += 20;
        line(x, y + 2, "Trailer style = shaders + Bare Bones + Fresh Animations", Glass.DIM); y += 18;
        line(x, y, "Resource packs (click to toggle, reloads resources)", Glass.ACCENT); y += 14;
        List<ResourcePackProfile> packs = Features.userPacks();
        if (packs.isEmpty()) { line(x, y + 2, "No extra resource packs found in resourcepacks/.", Glass.DIM); y += 14; }
        int col = 0, cw = (w - 6) / 2;
        for (ResourcePackProfile p : packs) {
            String id = p.getId(), name = p.getDisplayName().getString();
            btn(x + col * (cw + 6), y, cw, () -> (Features.packEnabled(id) ? "✔ " : "  ") + trim(name, cw - 20), () -> Features.togglePack(id), () -> Features.packEnabled(id), true);
            if (++col == 2) { col = 0; y += 19; }
        }
        if (col != 0) y += 19;
        return y;
    }

    private int buildHud(int x, int y, int w) {
        line(x, y, "HUD elements", Glass.ACCENT); y += 14;
        for (String id : Hud.IDS) {
            btn(x, y, 200, () -> Hud.label(id) + ": " + (Hud.enabled(id) ? "ON" : "OFF"), () -> Hud.toggle(id), () -> Hud.enabled(id), true);
            y += 19;
        }
        y += 4;
        btn(x, y, 200, "Edit positions (drag)…", () -> client.setScreen(new HudEditScreen(this)), () -> false); y += 22;
        line(x, y, "Saved to config/vanta-companion.json", Glass.DIM);
        return y + 14;
    }

    private int buildPerf(int x, int y, int w) {
        line(x, y, "Quick presets (view/sim distance, graphics, shadows, clouds, biome blend)", Glass.ACCENT); y += 16;
        String[] desc = {"4 chunks · Fast · no shadows/clouds", "8 chunks · Fast · fast clouds", "12 chunks · Fancy · blend 2", "20 chunks · Fabulous · blend 3"};
        for (int i = 0; i < Features.PRESETS.length; i++) {
            String p = Features.PRESETS[i];
            btn(x, y, 110, p, () -> { Features.applyPreset(p); status = p + " preset applied"; }, () -> p.equals(VantaConfig.get().lastPreset));
            line(x + 118, y + 4, desc[i], Glass.DIM);
            y += 20;
        }
        lines.add(new Line(x, y + 6, () -> "Current FPS: " + client.getCurrentFps(), Glass.SILVER, true));
        return y + 20;
    }

    private int buildFullbright(int x, int y, int w) {
        btn(x, y, 200, () -> "Fullbright: " + (VantaConfig.get().fullbright ? "ON" : "OFF"), () -> status = Features.setFullbright(!VantaConfig.get().fullbright), () -> VantaConfig.get().fullbright, true);
        y += 22;
        line(x, y, Compat.gammaUtilsLoaded() ? "Using Gamma Utils' night vision toggle." : "Gamma Utils not installed: applying a client-side Night Vision effect.", Glass.DIM);
        return y + 14;
    }

    private String trim(String s, int px) {
        if (textRenderer.getWidth(s) <= px) return s;
        while (s.length() > 1 && textRenderer.getWidth(s + "…") > px) s = s.substring(0, s.length() - 1);
        return s + "…";
    }

    @Override public void renderBackground(DrawContext c, int mx, int my, float d) {
        if (client.world == null) renderPanoramaBackground(c, d);
        c.fillGradient(0, 0, width, height, 0xA0120820, 0xC0050208);
    }

    @Override public void render(DrawContext c, int mx, int my, float d) {
        super.render(c, mx, my, d);
        Glass.panel(c, px1, py1, px2, py2);
        c.drawTextWithShadow(textRenderer, "VANTA", px1 + 10, py1 + 8, Glass.SILVER);
        c.drawTextWithShadow(textRenderer, "Companion", px1 + 10 + textRenderer.getWidth("VANTA "), py1 + 8, Glass.ACCENT);
        c.drawTextWithShadow(textRenderer, "Right Shift", px2 - 10 - textRenderer.getWidth("Right Shift"), py1 + 8, Glass.DIM);
        c.fill(px1 + 8, cy1 - 3, px2 - 8, cy1 - 2, 0x55C9CAD6);
        for (Btn b : buttons) if (!b.scroll) drawBtn(c, b, 0, mx, my);
        c.enableScissor(px1 + 2, cy1, px2 - 2, cy2);
        for (Line l : lines) c.drawTextWithShadow(textRenderer, l.text.get(), l.x, l.y - scroll, l.color);
        for (Btn b : buttons) if (b.scroll) drawBtn(c, b, scroll, mx, my);
        c.disableScissor();
        int max = contentH - (cy2 - cy1);
        if (max > 0) {
            int track = cy2 - cy1, bar = Math.max(16, track * track / contentH), by = cy1 + (track - bar) * scroll / max;
            c.fill(px2 - 5, cy1, px2 - 3, cy2, 0x33FFFFFF); c.fill(px2 - 5, by, px2 - 3, by + bar, 0xAAC9CAD6);
        }
        c.drawTextWithShadow(textRenderer, status, px1 + 10, py2 - 14, Glass.WARN);
    }

    private void drawBtn(DrawContext c, Btn b, int off, int mx, int my) {
        int y = b.y - off;
        boolean hover = b.enabled && in(mx, my, b.x, y, b.w, b.h) && (!b.scroll || (my >= cy1 && my < cy2));
        Glass.button(c, b.x, y, b.w, b.h, hover, b.active.getAsBoolean(), b.enabled);
        String s = b.label.get();
        c.drawCenteredTextWithShadow(textRenderer, s, b.x + b.w / 2, y + (b.h - 8) / 2, b.enabled ? Glass.TEXT : Glass.DIM);
    }

    private static boolean in(double mx, double my, int x, int y, int w, int h) { return mx >= x && mx < x + w && my >= y && my < y + h; }

    @Override public boolean mouseClicked(Click click, boolean doubled) {
        if (click.button() == 0) for (Btn b : List.copyOf(buttons)) {
            int y = b.scroll ? b.y - scroll : b.y;
            if (b.scroll && (click.y() < cy1 || click.y() >= cy2)) continue;
            if (b.enabled && in(click.x(), click.y(), b.x, y, b.w, b.h)) {
                try { b.action.run(); } catch (Throwable t) { status = "Error: " + t.getMessage(); VantaCompanion.LOG.error("Action failed", t); }
                return true;
            }
        }
        return super.mouseClicked(click, doubled);
    }

    @Override public boolean mouseScrolled(double mx, double my, double h, double v) {
        int max = Math.max(0, contentH - (cy2 - cy1));
        scroll = (int) Math.max(0, Math.min(max, scroll - v * 18));
        return true;
    }

    @Override public boolean keyPressed(KeyInput k) {
        if (VantaCompanion.openKey.matchesKey(k)) { close(); return true; }
        return super.keyPressed(k);
    }

    @Override public void close() { client.setScreen(parent); }
    @Override public boolean shouldPause() { return true; }
}
