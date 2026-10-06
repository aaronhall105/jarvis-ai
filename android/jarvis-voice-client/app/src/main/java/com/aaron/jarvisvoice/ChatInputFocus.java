package com.aaron.jarvisvoice;

import android.content.Context;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;

/** Keeps typed chat composition stable while messages and streaming content update. */
final class ChatInputFocus {
    private ChatInputFocus() {}

    static void retainAfterSend(EditText input) {
        input.requestFocus();
        input.setSelection(input.length());
        input.post(() -> {
            if (!input.isAttachedToWindow() || !input.hasFocus()) return;
            InputMethodManager keyboard = (InputMethodManager) input.getContext()
                .getSystemService(Context.INPUT_METHOD_SERVICE);
            if (keyboard != null) keyboard.showSoftInput(input, InputMethodManager.SHOW_IMPLICIT);
        });
    }
}
