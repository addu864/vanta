package addu.vanta.companion;

import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.entrypoint.EntrypointContainer;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.option.KeyBinding;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.Map;

/**
 * All optional-mod integration lives here and goes through reflection only, so there is
 * no compile-time or class-loading link to Iris, Mod Menu or Gamma Utils. Every call is
 * wrapped so a missing mod or a changed API degrades to "unavailable" instead of a crash.
 */
public final class Compat {
    private Compat() {}

    public static boolean loaded(String id) { return FabricLoader.getInstance().isModLoaded(id); }

    // ---------------- Iris ----------------
    public static boolean irisAvailable() { return loaded("iris") && irisConfig() != null; }

    private static Object irisConfig() {
        try {
            Class<?> api = Class.forName("net.irisshaders.iris.api.v0.IrisApi");
            Object inst = api.getMethod("getInstance").invoke(null);
            return api.getMethod("getConfig").invoke(inst);
        } catch (Throwable t) { return null; }
    }

    public static boolean shadersEnabled() {
        try {
            Object cfg = irisConfig();
            if (cfg == null) return false;
            Class<?> c = Class.forName("net.irisshaders.iris.api.v0.IrisApiConfig");
            return (Boolean) c.getMethod("areShadersEnabled").invoke(cfg);
        } catch (Throwable t) { return false; }
    }

    public static boolean setShaders(boolean on) {
        try {
            Object cfg = irisConfig();
            if (cfg == null) return false;
            Class<?> c = Class.forName("net.irisshaders.iris.api.v0.IrisApiConfig");
            c.getMethod("setShadersEnabledAndApply", boolean.class).invoke(cfg, on);
            return true;
        } catch (Throwable t) { VantaCompanion.LOG.warn("Iris toggle failed", t); return false; }
    }

    // ---------------- Mod Menu ----------------
    private static Map<String, Object> modMenuFactories;

    private static Map<String, Object> factories() {
        if (modMenuFactories != null) return modMenuFactories;
        Map<String, Object> map = new HashMap<>();
        if (loaded("modmenu")) {
            try {
                Class<?> apiCls = Class.forName("com.terraformersmc.modmenu.api.ModMenuApi");
                Method single = apiCls.getMethod("getModConfigScreenFactory");
                Method provided = apiCls.getMethod("getProvidedConfigScreenFactories");
                for (EntrypointContainer<Object> ep : FabricLoader.getInstance().getEntrypointContainers("modmenu", Object.class)) {
                    try {
                        Object api = ep.getEntrypoint();
                        if (!apiCls.isInstance(api)) continue;
                        Object f = single.invoke(api);
                        if (f != null) map.put(ep.getProvider().getMetadata().getId(), f);
                        Object extra = provided.invoke(api);
                        if (extra instanceof Map<?, ?> m) m.forEach((k, v) -> map.putIfAbsent(String.valueOf(k), v));
                    } catch (Throwable t) { VantaCompanion.LOG.debug("Mod Menu entrypoint failed", t); }
                }
            } catch (Throwable t) { VantaCompanion.LOG.warn("Mod Menu API not usable", t); }
        }
        modMenuFactories = map;
        return map;
    }

    public static boolean hasConfigScreen(String modId) { return factories().containsKey(modId); }

    public static Screen configScreen(String modId, Screen parent) {
        Object f = factories().get(modId);
        if (f == null) return null;
        try {
            Class<?> fc = Class.forName("com.terraformersmc.modmenu.api.ConfigScreenFactory");
            Object s = fc.getMethod("create", Screen.class).invoke(f, parent);
            return s instanceof Screen sc ? sc : null;
        } catch (Throwable t) { VantaCompanion.LOG.warn("Could not open config for " + modId, t); return null; }
    }

    // ---------------- Gamma Utils ----------------
    public static boolean gammaUtilsLoaded() { return loaded("gammautils"); }

    /** Tries Gamma Utils' night-vision / gamma toggle. Returns true if something was triggered. */
    public static boolean gammaUtilsToggle(boolean wantOn) {
        if (!gammaUtilsLoaded()) return false;
        String[] classes = {"com.sjouwer.gammautils.NightVisionManager", "com.sjouwer.gammautils.GammaManager", "com.sjouwer.gammautils.util.GammaManager"};
        String[] methods = wantOn ? new String[]{"enableNightVision", "toggleNightVision", "enableGamma", "toggleGamma"}
                                  : new String[]{"disableNightVision", "toggleNightVision", "disableGamma", "toggleGamma"};
        for (String cn : classes) {
            try {
                Class<?> c = Class.forName(cn);
                for (String mn : methods) {
                    try {
                        Method m = c.getMethod(mn);
                        Object target = java.lang.reflect.Modifier.isStatic(m.getModifiers()) ? null : instanceOf(c);
                        if (target == null && !java.lang.reflect.Modifier.isStatic(m.getModifiers())) continue;
                        m.invoke(target);
                        return true;
                    } catch (NoSuchMethodException ignored) {}
                }
            } catch (Throwable ignored) {}
        }
        // Fallback: press Gamma Utils' own night-vision (or gamma) toggle keybinding for one tick.
        KeyBinding kb = findKey("gammautils", "night");
        if (kb == null) kb = findKey("gammautils", "toggle");
        if (kb != null) {
            KeyBinding.onKeyPressed(net.minecraft.client.util.InputUtil.fromTranslationKey(kb.getBoundKeyTranslationKey()));
            return true;
        }
        return false;
    }

    private static Object instanceOf(Class<?> c) {
        try { return c.getMethod("getInstance").invoke(null); } catch (Throwable ignored) {}
        try { Field f = c.getField("INSTANCE"); return f.get(null); } catch (Throwable ignored) {}
        return null;
    }

    private static KeyBinding findKey(String a, String b) {
        for (KeyBinding k : MinecraftClient.getInstance().options.allKeys) {
            String id = k.getId().toLowerCase();
            if (id.contains(a) && id.contains(b) && !k.isUnbound()) return k;
        }
        return null;
    }
}
