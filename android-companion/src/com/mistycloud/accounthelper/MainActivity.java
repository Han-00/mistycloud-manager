package com.mistycloud.accounthelper;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipDescription;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.PersistableBundle;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Register one account on demand and let the user copy its credentials. */
public final class MainActivity extends Activity {
    private static final int BACKGROUND = Color.rgb(244, 248, 248);
    private static final int INK = Color.rgb(25, 46, 54);
    private static final int MUTED = Color.rgb(78, 98, 105);
    private static final int ACCENT = Color.rgb(5, 121, 108);
    private static final int ACCENT_TINT = Color.rgb(226, 243, 239);
    private static final int FIELD = Color.rgb(245, 249, 249);
    private static final int BORDER = Color.rgb(222, 232, 231);
    private static final int ERROR = Color.rgb(143, 43, 43);
    private static final int ERROR_TINT = Color.rgb(252, 237, 236);

    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private RegistrationStore store;
    private RegistrationStore.State state;
    private Button registerButton;
    private TextView status;
    private TextView emailView;
    private TextView passwordView;
    private TextView registeredAtView;
    private TextView tryNewMailbox;
    private LinearLayout resultCard;
    private boolean busy;
    private boolean storeReady;
    private boolean lastAttemptFailed;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        getWindow().setStatusBarColor(BACKGROUND);
        getWindow().setNavigationBarColor(BACKGROUND);
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        store = new RegistrationStore(this);
        try {
            state = store.load();
            storeReady = true;
        } catch (Exception e) {
            state = new RegistrationStore.State();
            storeReady = false;
        }
        buildScreen();
        status.setText(storeReady ? "" : "无法读取本机加密数据，请暂勿注册，以免覆盖原账号。");
        render();
    }

    @Override protected void onDestroy() {
        worker.shutdownNow();
        super.onDestroy();
    }

    private void buildScreen() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(BACKGROUND);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(20), dp(24), dp(20), dp(32));
        scroll.addView(root);
        setContentView(scroll);

        LinearLayout brand = new LinearLayout(this);
        brand.setOrientation(LinearLayout.HORIZONTAL);
        brand.setGravity(Gravity.CENTER_VERTICAL);
        TextView mark = text("M", 19, Color.WHITE, true);
        mark.setGravity(Gravity.CENTER);
        mark.setBackground(shape(ACCENT, 11, 0));
        brand.addView(mark, new LinearLayout.LayoutParams(dp(38), dp(38)));
        TextView brandName = text("Misty 快速注册", 18, INK, true);
        LinearLayout.LayoutParams nameParams = new LinearLayout.LayoutParams(-2, -2);
        nameParams.setMargins(dp(11), 0, 0, 0);
        brand.addView(brandName, nameParams);
        root.addView(brand);

        TextView heading = text("注册一个新账号", 27, INK, true);
        heading.setPadding(0, dp(32), 0, dp(8));
        root.addView(heading);
        TextView intro = text("自动收取验证码。完成后，复制账号和密码到 Misty 登录。", 15, MUTED, false);
        intro.setLineSpacing(dp(3), 1f);
        intro.setPadding(0, 0, 0, dp(24));
        root.addView(intro);

        registerButton = button("注册新账号", () -> registerOne());
        registerButton.setTextSize(17);
        registerButton.setTextColor(Color.WHITE);
        registerButton.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        registerButton.setBackground(ripple(ACCENT, 14));
        registerButton.setMinHeight(dp(56));
        registerButton.setPadding(dp(16), dp(10), dp(16), dp(10));
        root.addView(registerButton);

        Button overlayButton = button("开启 Misty 换号悬浮按钮", () ->
                startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        overlayButton.setTextSize(15);
        overlayButton.setTextColor(ACCENT);
        overlayButton.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        overlayButton.setBackground(ripple(ACCENT_TINT, 12));
        overlayButton.setMinHeight(dp(50));
        LinearLayout.LayoutParams overlayParams = new LinearLayout.LayoutParams(-1, -2);
        overlayParams.setMargins(0, dp(10), 0, 0);
        root.addView(overlayButton, overlayParams);

        status = text("", 14, ACCENT, false);
        status.setLineSpacing(dp(2), 1f);
        status.setPadding(dp(14), dp(13), dp(14), dp(13));
        LinearLayout.LayoutParams statusParams = new LinearLayout.LayoutParams(-1, -2);
        statusParams.setMargins(0, dp(14), 0, 0);
        root.addView(status, statusParams);

        tryNewMailbox = text("换一个邮箱重新注册", 14, ACCENT, false);
        tryNewMailbox.setMinHeight(dp(44));
        tryNewMailbox.setGravity(Gravity.CENTER_VERTICAL);
        tryNewMailbox.setPadding(dp(4), dp(8), dp(4), dp(8));
        tryNewMailbox.setOnClickListener(v -> replaceMailbox());
        root.addView(tryNewMailbox);

        resultCard = new LinearLayout(this);
        resultCard.setOrientation(LinearLayout.VERTICAL);
        resultCard.setPadding(dp(16), dp(18), dp(16), dp(18));
        resultCard.setBackground(shape(Color.WHITE, 16, BORDER));
        LinearLayout.LayoutParams cardParams = new LinearLayout.LayoutParams(-1, -2);
        cardParams.setMargins(0, dp(22), 0, dp(12));
        root.addView(resultCard, cardParams);

        LinearLayout resultHeader = new LinearLayout(this);
        resultHeader.setOrientation(LinearLayout.HORIZONTAL);
        resultHeader.setGravity(Gravity.CENTER_VERTICAL);
        TextView check = text("✓", 19, ACCENT, true);
        check.setGravity(Gravity.CENTER);
        check.setBackground(shape(ACCENT_TINT, 10, 0));
        resultHeader.addView(check, new LinearLayout.LayoutParams(dp(34), dp(34)));
        TextView resultTitle = text("已注册，可直接登录", 19, INK, true);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(-2, -2);
        titleParams.setMargins(dp(10), 0, 0, 0);
        resultHeader.addView(resultTitle, titleParams);
        resultCard.addView(resultHeader);

        registeredAtView = text("", 13, MUTED, false);
        registeredAtView.setPadding(0, dp(12), 0, dp(18));
        resultCard.addView(registeredAtView);
        resultCard.addView(credentialField("登录账号", true));
        LinearLayout.LayoutParams passwordParams = new LinearLayout.LayoutParams(-1, -2);
        passwordParams.setMargins(0, dp(10), 0, 0);
        resultCard.addView(credentialField("登录密码", false), passwordParams);

        TextView replaceNote = text("下次注册成功后，这里只显示新账号。请先保存当前账号。", 12, MUTED, false);
        replaceNote.setLineSpacing(dp(2), 1f);
        replaceNote.setPadding(dp(2), dp(16), dp(2), 0);
        resultCard.addView(replaceNote);

        TextView mailboxNote = text("使用临时邮箱注册，请保存好密码，以便以后登录。", 13, MUTED, false);
        mailboxNote.setLineSpacing(dp(2), 1f);
        mailboxNote.setPadding(dp(2), dp(18), dp(2), dp(8));
        root.addView(mailboxNote);
        TextView credit = text("临时邮箱由 mail.tm 提供，点此了解", 12, ACCENT, false);
        credit.setPadding(dp(2), dp(4), dp(2), dp(4));
        credit.setOnClickListener(v -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://mail.tm"))));
        root.addView(credit);
    }

    private void render() {
        registerButton.setEnabled(storeReady && !busy);
        registerButton.setAlpha(storeReady && !busy ? 1f : 0.58f);
        registerButton.setText(state.pendingEmail.isEmpty() ? "注册新账号" : "继续上次注册");
        boolean hasResult = !state.lastEmail.isEmpty() && !state.lastPassword.isEmpty();
        resultCard.setVisibility(hasResult ? View.VISIBLE : View.GONE);
        if (hasResult) {
            emailView.setText(state.lastEmail);
            passwordView.setText(state.lastPassword);
            registeredAtView.setText(state.registeredAtMs > 0
                    ? "注册时间（本机）："
                        + new SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.CHINA).format(new Date(state.registeredAtMs))
                    : "注册时间：旧版未记录");
        }
        status.setVisibility(!storeReady || busy || lastAttemptFailed ? View.VISIBLE : View.GONE);
        status.setTextColor(lastAttemptFailed || !storeReady ? ERROR : ACCENT);
        status.setBackground(shape(lastAttemptFailed || !storeReady ? ERROR_TINT : ACCENT_TINT, 12, 0));
        tryNewMailbox.setVisibility(storeReady && !busy && lastAttemptFailed && !state.pendingEmail.isEmpty()
                ? View.VISIBLE : View.GONE);
    }

    private void registerOne() {
        if (busy || !storeReady) return;
        lastAttemptFailed = false;
        setBusy(state.pendingEmail.isEmpty() ? "正在创建临时邮箱…" : "正在检查上次注册结果…");
        RegistrationStore.State working = state.copy();
        worker.execute(() -> {
            try {
                RegistrationService registration = new RegistrationService();
                RegistrationService.Mailbox mailbox;
                if (working.pendingEmail.isEmpty()) {
                    mailbox = registration.createMailbox();
                    working.pendingEmail = mailbox.email;
                    working.pendingPassword = mailbox.password;
                    store.save(working);
                    runOnUiThread(() -> { state = working.copy(); render(); });
                } else {
                    mailbox = new RegistrationService.Mailbox(working.pendingEmail, working.pendingPassword);
                    if (registration.canLogin(mailbox)) {
                        complete(working, mailbox, false);
                        return;
                    }
                }
                registration.register(mailbox, message -> runOnUiThread(() -> status.setText(message)));
                runOnUiThread(() -> status.setText("正在确认新账号可以登录…"));
                if (!registration.canLogin(mailbox)) {
                    throw new RegistrationService.Failure("注册已提交，但暂时无法确认登录。请点“继续上次注册”重试。");
                }
                complete(working, mailbox, true);
            } catch (Exception error) {
                String message = error instanceof RegistrationService.Failure
                        ? error.getMessage() : "本机保存或网络操作失败，请重试。";
                runOnUiThread(() -> {
                    lastAttemptFailed = true;
                    setIdle("注册未完成：" + message);
                });
            }
        });
    }

    private void complete(RegistrationStore.State working, RegistrationService.Mailbox mailbox,
                          boolean completedNow) throws Exception {
        working.lastEmail = mailbox.email;
        working.lastPassword = mailbox.password;
        working.registeredAtMs = completedNow ? System.currentTimeMillis() : 0L;
        working.pendingEmail = "";
        working.pendingPassword = "";
        store.save(working);
        runOnUiThread(() -> {
            state = working.copy();
            lastAttemptFailed = false;
            setIdle("注册成功，账号和密码已显示在下方。点一下即可复制。");
        });
    }

    private void replaceMailbox() {
        if (busy || !storeReady || state.pendingEmail.isEmpty()) return;
        new AlertDialog.Builder(this)
                .setTitle("换一个邮箱？")
                .setMessage("会放弃这次未完成的邮箱，再创建一个新邮箱。")
                .setNegativeButton("取消", null)
                .setPositiveButton("重新注册", (dialog, which) -> {
                    RegistrationStore.State next = state.copy();
                    next.pendingEmail = "";
                    next.pendingPassword = "";
                    try {
                        store.save(next);
                        state = next;
                        registerOne();
                    } catch (Exception e) {
                        status.setText("无法保存本机数据，请稍后重试。");
                    }
                }).show();
    }

    private void copy(String label, String value) {
        if (value == null || value.isEmpty()) return;
        ClipData clip = ClipData.newPlainText("Misty " + label, value);
        PersistableBundle extras = new PersistableBundle();
        extras.putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true);
        clip.getDescription().setExtras(extras);
        ((ClipboardManager) getSystemService(CLIPBOARD_SERVICE)).setPrimaryClip(clip);
        Toast.makeText(this, label + "已复制", Toast.LENGTH_SHORT).show();
    }

    private void setBusy(String message) { busy = true; status.setText(message); render(); }
    private void setIdle(String message) { busy = false; status.setText(message); render(); }

    private Button button(String label, Runnable action) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(15);
        button.setAllCaps(false);
        button.setOnClickListener(v -> action.run());
        return button;
    }

    private TextView text(String value, int size, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setGravity(Gravity.START);
        if (bold) view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        return view;
    }

    private LinearLayout credentialField(String label, boolean email) {
        LinearLayout field = new LinearLayout(this);
        field.setOrientation(LinearLayout.VERTICAL);
        field.setPadding(dp(14), dp(12), dp(12), dp(12));
        field.setMinimumHeight(dp(86));
        field.setBackground(ripple(FIELD, 12));
        field.setFocusable(true);
        field.setOnClickListener(v -> copy(email ? "账号" : "密码",
                email ? state.lastEmail : state.lastPassword));

        field.addView(text(label, 12, MUTED, true));
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(7), 0, 0);
        field.addView(row);

        TextView value = credentialText();
        value.setOnClickListener(v -> copy(email ? "账号" : "密码",
                email ? state.lastEmail : state.lastPassword));
        row.addView(value, new LinearLayout.LayoutParams(0, -2, 1f));
        if (email) emailView = value;
        else passwordView = value;

        TextView copyAction = text("复制", 14, ACCENT, true);
        copyAction.setGravity(Gravity.CENTER);
        copyAction.setBackground(ripple(ACCENT_TINT, 9));
        copyAction.setOnClickListener(v -> copy(email ? "账号" : "密码",
                email ? state.lastEmail : state.lastPassword));
        LinearLayout.LayoutParams copyParams = new LinearLayout.LayoutParams(dp(54), dp(38));
        copyParams.setMargins(dp(8), 0, 0, 0);
        row.addView(copyAction, copyParams);
        return field;
    }

    private TextView credentialText() {
        TextView view = text("", 15, INK, true);
        view.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        view.setMaxLines(3);
        return view;
    }

    private GradientDrawable shape(int fill, int radius, int stroke) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(fill);
        drawable.setCornerRadius(dp(radius));
        if (stroke != 0) drawable.setStroke(dp(1), stroke);
        return drawable;
    }

    private RippleDrawable ripple(int fill, int radius) {
        return new RippleDrawable(ColorStateList.valueOf(Color.argb(35, 0, 0, 0)),
                shape(fill, radius, 0), null);
    }

    private int dp(int value) { return Math.round(getResources().getDisplayMetrics().density * value); }
}
