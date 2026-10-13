package addu.vanta.companion;

import net.minecraft.client.MinecraftClient;
import net.minecraft.client.option.CloudRenderMode;
import net.minecraft.client.option.GameOptions;
import net.minecraft.client.option.GraphicsMode;
import net.minecraft.entity.effect.StatusEffectInstance;
import net.minecraft.entity.effect.StatusEffects;
import net.minecraft.resource.ResourcePackManager;
import net.minecraft.resource.ResourcePackProfile;

import java.util.*;

public final class Features {
    private Features() {}
    private static boolean weAppliedNightVision;

    // ---------- Resource packs ----------
    public static List<ResourcePackProfile> userPacks() {
        ResourcePackManager m = MinecraftClient.getInstance().getResourcePackManager();
        List<ResourcePackProfile> out = new ArrayList<>();
        for (ResourcePackProfile p : m.getProfiles()) {
            String id = p.getId();
            if (id.equals("vanilla") || id.startsWith("fabric") || p.isRequired()) continue;
            out.add(p);
        }
        out.sort(Comparator.comparing(p -> p.getDisplayName().getString().toLowerCase(Locale.ROOT)));
        return out;
    }

    public static boolean packEnabled(String id) { return MinecraftClient.getInstance().getResourcePackManager().getEnabledIds().contains(id); }

    public static void setPacks(Map<String, Boolean> changes) {
        MinecraftClient mc = MinecraftClient.getInstance();
        ResourcePackManager m = mc.getResourcePackManager();
        boolean changed = false;
        for (var e : changes.entrySet()) changed |= e.getValue() ? m.enable(e.getKey()) : m.disable(e.getKey());
        if (!changed) return;
        mc.options.refreshResourcePacks(m); // writes options.txt and reloads resources
    }

    public static void togglePack(String id) { setPacks(Map.of(id, !packEnabled(id))); }

    private static List<String> matchPacks(String... needles) {
        List<String> ids = new ArrayList<>();
        for (ResourcePackProfile p : userPacks()) {
            String hay = (p.getId() + " " + p.getDisplayName().getString()).toLowerCase(Locale.ROOT).replaceAll("[^a-z]", "");
            for (String n : needles) if (hay.contains(n)) { ids.add(p.getId()); break; }
        }
        return ids;
    }

    public static List<String> bareBonesPacks() { return matchPacks("barebones"); }
    public static List<String> freshAnimationsPacks() { return matchPacks("freshanimations"); }

    /** Trailer style = shaders on (Iris) + Bare Bones + Fresh Animations. Returns a status line. */
    public static String setTrailerStyle(boolean on) {
        List<String> bb = bareBonesPacks(), fa = freshAnimationsPacks();
        Map<String, Boolean> ch = new LinkedHashMap<>();
        // Fresh Animations must sit above Bare Bones; enable() appends, so enable BB first.
        for (String id : bb) ch.put(id, on);
        for (String id : fa) ch.put(id, on);
        setPacks(ch);
        boolean shaders = Compat.irisAvailable() && Compat.setShaders(on);
        VantaConfig.get().trailerStyle = on; VantaConfig.get().save();
        List<String> missing = new ArrayList<>();
        if (bb.isEmpty()) missing.add("Bare Bones");
        if (fa.isEmpty()) missing.add("Fresh Animations");
        if (!Compat.irisAvailable()) missing.add("Iris");
        return (on ? "Trailer style on" : "Trailer style off") + (missing.isEmpty() ? "" : " (missing: " + String.join(", ", missing) + ")");
    }

    // ---------- Performance presets ----------
    public static final String[] PRESETS = {"Potato", "Balanced", "Quality", "Ultra"};

    public static void applyPreset(String name) {
        GameOptions o = MinecraftClient.getInstance().options;
        switch (name) {
            case "Potato" -> set(o, 4, 5, GraphicsMode.FAST, false, CloudRenderMode.OFF, 0);
            case "Balanced" -> set(o, 8, 8, GraphicsMode.FAST, true, CloudRenderMode.FAST, 1);
            case "Quality" -> set(o, 12, 10, GraphicsMode.FANCY, true, CloudRenderMode.FANCY, 2);
            case "Ultra" -> set(o, 20, 12, GraphicsMode.FABULOUS, true, CloudRenderMode.FANCY, 3);
            default -> {}
        }
        o.write();
        MinecraftClient.getInstance().worldRenderer.reload();
        VantaConfig.get().lastPreset = name; VantaConfig.get().save();
    }

    private static void set(GameOptions o, int view, int sim, GraphicsMode g, boolean shadows, CloudRenderMode clouds, int blend) {
        o.getPreset().setValue(g);
        o.getViewDistance().setValue(view);
        o.getSimulationDistance().setValue(sim);
        o.getEntityShadows().setValue(shadows);
        o.getCloudRenderMode().setValue(clouds);
        o.getBiomeBlendRadius().setValue(blend);
    }

    // ---------- Fullbright ----------
    public static String setFullbright(boolean on) {
        VantaConfig.get().fullbright = on; VantaConfig.get().save();
        if (Compat.gammaUtilsLoaded() && Compat.gammaUtilsToggle(on)) return on ? "Fullbright on (Gamma Utils)" : "Fullbright off (Gamma Utils)";
        if (!on) removeNightVision();
        return on ? "Fullbright on (client night vision)" : "Fullbright off";
    }

    /** Called every client tick: keeps a client-side night vision effect alive when Gamma Utils isn't handling it. */
    public static void tick(MinecraftClient mc) {
        if (mc.player == null) { weAppliedNightVision = false; return; }
        if (!VantaConfig.get().fullbright || Compat.gammaUtilsLoaded()) return;
        StatusEffectInstance cur = mc.player.getStatusEffect(StatusEffects.NIGHT_VISION);
        if (cur == null || (weAppliedNightVision && cur.getDuration() < 400)) {
            mc.player.addStatusEffect(new StatusEffectInstance(StatusEffects.NIGHT_VISION, 1200, 0, true, false, false));
            weAppliedNightVision = true;
        }
    }

    private static void removeNightVision() {
        MinecraftClient mc = MinecraftClient.getInstance();
        if (mc.player != null && weAppliedNightVision) mc.player.removeStatusEffect(StatusEffects.NIGHT_VISION);
        weAppliedNightVision = false;
    }
}
