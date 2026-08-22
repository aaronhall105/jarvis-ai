package com.aaron.jarvisvoice.wear;

import androidx.wear.protolayout.ActionBuilders;
import androidx.wear.protolayout.ColorBuilders;
import androidx.wear.protolayout.DimensionBuilders;
import androidx.wear.protolayout.LayoutElementBuilders;
import androidx.wear.protolayout.ModifiersBuilders;
import androidx.wear.protolayout.TimelineBuilders;
import androidx.wear.tiles.RequestBuilders;
import androidx.wear.tiles.TileBuilders;
import androidx.wear.tiles.TileService;
import com.google.common.util.concurrent.Futures;
import com.google.common.util.concurrent.ListenableFuture;

public final class JarvisTileService extends TileService {
    @Override public ListenableFuture<TileBuilders.Tile> onTileRequest(RequestBuilders.TileRequest request) {
        ActionBuilders.LaunchAction launch = new ActionBuilders.LaunchAction.Builder()
            .setAndroidActivity(new ActionBuilders.AndroidActivity.Builder().setPackageName(getPackageName()).setClassName(JarvisWearActivity.class.getName()).addKeyToExtraMapping(JarvisWearActivity.EXTRA_AUTO_START, new ActionBuilders.AndroidBooleanExtra.Builder().setValue(true).build()).build()).build();
        ModifiersBuilders.Modifiers modifiers = new ModifiersBuilders.Modifiers.Builder().setClickable(new ModifiersBuilders.Clickable.Builder().setId("start_jarvis").setOnClick(launch).build()).build();
        LayoutElementBuilders.Column layout = new LayoutElementBuilders.Column.Builder().setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER)
            .addContent(new LayoutElementBuilders.Text.Builder().setText("JARVIS").setFontStyle(new LayoutElementBuilders.FontStyle.Builder().setSize(DimensionBuilders.sp(22)).setColor(ColorBuilders.argb(0xffffffff)).build()).build())
            .addContent(new LayoutElementBuilders.Spacer.Builder().setHeight(DimensionBuilders.dp(12)).build())
            .addContent(new LayoutElementBuilders.Text.Builder().setText("🎙  Tap to talk").setFontStyle(new LayoutElementBuilders.FontStyle.Builder().setSize(DimensionBuilders.sp(18)).setColor(ColorBuilders.argb(0xff55d6ff)).build()).setModifiers(modifiers).build()).build();
        return Futures.immediateFuture(new TileBuilders.Tile.Builder().setResourcesVersion("1").setTileTimeline(new TimelineBuilders.Timeline.Builder().addTimelineEntry(new TimelineBuilders.TimelineEntry.Builder().setLayout(new LayoutElementBuilders.Layout.Builder().setRoot(layout).build()).build()).build()).build());
    }
}
