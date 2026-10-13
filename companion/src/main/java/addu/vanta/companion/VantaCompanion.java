package addu.vanta.companion;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.keybinding.v1.KeyBindingHelper;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.fabric.api.client.screen.v1.Screens;
import net.minecraft.client.gui.screen.GameMenuScreen;
import net.minecraft.client.gui.screen.TitleScreen;
import net.minecraft.client.gui.widget.ButtonWidget;
import net.minecraft.client.option.KeyBinding;
import net.minecraft.text.Text;
import net.minecraft.util.Identifier;
import org.lwjgl.glfw.GLFW;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class VantaCompanion implements ClientModInitializer {
    public static final String MOD_ID = "vanta-companion";
    public static final Logger LOG = LoggerFactory.getLogger("Vanta Companion");
    public static KeyBinding openKey;

    @Override
    public void onInitializeClient() {
        VantaConfig.load();
        openKey = KeyBindingHelper.registerKeyBinding(new KeyBinding("key.vanta-companion.open", GLFW.GLFW_KEY_RIGHT_SHIFT,
                KeyBinding.Category.create(Identifier.of(MOD_ID, "main"))));

        ClientTickEvents.END_CLIENT_TICK.register(mc -> {
            while (openKey.wasPressed()) if (mc.currentScreen == null) mc.setScreen(new VantaScreen(null));
            Features.tick(mc);
        });

        HudElementRegistry.addLast(Identifier.of(MOD_ID, "hud"), (ctx, tick) -> Hud.render(ctx));

        ScreenEvents.AFTER_INIT.register((client, screen, w, h) -> {
            int x, y;
            if (screen instanceof TitleScreen) { x = w / 2 - 100 - 56; y = h / 4 + 48; }
            else if (screen instanceof GameMenuScreen gm && gm.shouldShowMenu()) { x = w / 2 - 102 - 56; y = h / 4 + 8; }
            else return;
            Screens.getButtons(screen).add(ButtonWidget.builder(Text.literal("Vanta"), b -> client.setScreen(new VantaScreen(screen)))
                    .dimensions(x, y, 52, 20).build());
        });
        LOG.info("Vanta Companion ready (iris={}, modmenu={}, gammautils={})",
                Compat.loaded("iris"), Compat.loaded("modmenu"), Compat.loaded("gammautils"));
    }
}
