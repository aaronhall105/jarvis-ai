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
        ModifiersBuilders.Modifiers modifiers = new ModifiersBuilders.Modifiers.Builder()
            .setClickable(new ModifiersBuilders.Clickable.Builder().setId("start_jarvis").setOnClick(launch).build())
            .setBackground(new ModifiersBuilders.Background.Builder().setColor(ColorBuilders.argb(0xffffffff)).build())
            .build();
        ModifiersBuilders.Modifiers startButton = new ModifiersBuilders.Modifiers.Builder()
            .setBackground(new ModifiersBuilders.Background.Builder().setColor(ColorBuilders.argb(0xff141414)).setCorner(new ModifiersBuilders.Corner.Builder().setRadius(DimensionBuilders.dp(24)).build()).build())
            .setPadding(new ModifiersBuilders.Padding.Builder().setStart(DimensionBuilders.dp(22)).setEnd(DimensionBuilders.dp(22)).setTop(DimensionBuilders.dp(12)).setBottom(DimensionBuilders.dp(12)).build())
            .build();
        LayoutElementBuilders.Column layout = new LayoutElementBuilders.Column.Builder()
            .setWidth(DimensionBuilders.expand()).setHeight(DimensionBuilders.expand())
            .setHorizontalAlignment(LayoutElementBuilders.HORIZONTAL_ALIGN_CENTER).setModifiers(modifiers)
            .addContent(new LayoutElementBuilders.Spacer.Builder().setHeight(DimensionBuilders.dp(42)).build())
            .addContent(new LayoutElementBuilders.Text.Builder().setText("J A R V I S").setFontStyle(new LayoutElementBuilders.FontStyle.Builder().setSize(DimensionBuilders.sp(18)).setColor(ColorBuilders.argb(0xff141414)).setWeight(LayoutElementBuilders.FONT_WEIGHT_BOLD).build()).build())
            .addContent(new LayoutElementBuilders.Spacer.Builder().setHeight(DimensionBuilders.dp(18)).build())
            .addContent(new LayoutElementBuilders.Text.Builder().setText("MIC  ·  START").setFontStyle(new LayoutElementBuilders.FontStyle.Builder().setSize(DimensionBuilders.sp(15)).setColor(ColorBuilders.argb(0xffffffff)).build()).setModifiers(startButton).build())
            .addContent(new LayoutElementBuilders.Spacer.Builder().setHeight(DimensionBuilders.dp(12)).build())
            .addContent(new LayoutElementBuilders.Text.Builder().setText("Tap to talk").setFontStyle(new LayoutElementBuilders.FontStyle.Builder().setSize(DimensionBuilders.sp(13)).setColor(ColorBuilders.argb(0xff676767)).build()).build())
            .build();
        return Futures.immediateFuture(new TileBuilders.Tile.Builder().setResourcesVersion("1").setTileTimeline(new TimelineBuilders.Timeline.Builder().addTimelineEntry(new TimelineBuilders.TimelineEntry.Builder().setLayout(new LayoutElementBuilders.Layout.Builder().setRoot(layout).build()).build()).build()).build());
    }
}
